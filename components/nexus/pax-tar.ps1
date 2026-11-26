# Windows PowerShell 2.0 / .NET 2.0 compatible streaming POSIX pax tar writer for the Nexus backup.
# Self-contained: no local temporary archives, no external archive tools, long-path aware (\\?\).
# The C# below must compile with the .NET 2.0 csc.exe (see tests/nexus/test-csc20.ps1).
if (-not ('NexusPaxWriter' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;
public class NexusPaxWriter : IDisposable {
  const long MaxTarTime = 8589934591L;
  readonly Stream output;
  readonly SHA256 sha = new SHA256Managed();
  readonly CryptoStream hashing;
  readonly byte[] copyBuf = new byte[1 << 20];
  readonly byte[] zero = new byte[512];
  public long Entries;
  public long Bytes;
  public long DeadlineTicks;
  public string Sha256;
  public NexusPaxWriter(Stream output) {
    this.output = output;
    hashing = new CryptoStream(Stream.Null, sha, CryptoStreamMode.Write);
  }
  void Emit(byte[] b, int off, int len) {
    if (DeadlineTicks > 0 && DateTime.UtcNow.Ticks > DeadlineTicks) throw new IOException("Archive deadline exceeded");
    output.Write(b, off, len);
    hashing.Write(b, off, len);
    Bytes += len;
  }
  static bool HasNonAscii(string s) {
    foreach (char c in s) if (c > 127) return true;
    return false;
  }
  static string AsciiName(string s) {
    char[] c = s.ToCharArray();
    for (int i = 0; i < c.Length; i++) if (c[i] > 127) c[i] = '_';
    return new string(c);
  }
  static void PutText(byte[] h, int off, int len, string s) {
    byte[] b = Encoding.UTF8.GetBytes(s);
    Buffer.BlockCopy(b, 0, h, off, Math.Min(len, b.Length));
  }
  static void PutOctal(byte[] h, int off, int len, long v) {
    if (v < 0) throw new ArgumentException("Negative tar field");
    string s = Convert.ToString(v, 8);
    if (s.Length > len - 1) throw new ArgumentException("Tar octal field overflow");
    s = s.PadLeft(len - 1, '0');
    for (int i = 0; i < s.Length; i++) h[off + i] = (byte)s[i];
    h[off + len - 1] = 0;
  }
  static byte[] Header(string name, long size, long mtime, byte type) {
    byte[] h = new byte[512];
    PutText(h, 0, 100, name);
    PutOctal(h, 100, 8, 420);
    PutOctal(h, 108, 8, 0);
    PutOctal(h, 116, 8, 0);
    PutOctal(h, 124, 12, size);
    PutOctal(h, 136, 12, mtime);
    for (int i = 148; i < 156; i++) h[i] = 32;
    h[156] = type;
    PutText(h, 257, 6, "ustar");
    h[263] = (byte)'0';
    h[264] = (byte)'0';
    PutOctal(h, 329, 8, 0);
    PutOctal(h, 337, 8, 0);
    long sum = 0;
    for (int i = 0; i < 512; i++) sum += (int)h[i];
    string cs = Convert.ToString(sum, 8).PadLeft(6, '0');
    for (int i = 0; i < 6; i++) h[148 + i] = (byte)cs[i];
    h[154] = 0;
    h[155] = 32;
    return h;
  }
  static byte[] PaxRecord(string key, string value) {
    byte[] body = Encoding.UTF8.GetBytes(" " + key + "=" + value + "\n");
    int total = body.Length + 1;
    while (true) {
      int t = body.Length + total.ToString(CultureInfo.InvariantCulture).Length;
      if (t == total) break;
      total = t;
    }
    byte[] lenText = Encoding.ASCII.GetBytes(total.ToString(CultureInfo.InvariantCulture));
    byte[] rec = new byte[lenText.Length + body.Length];
    Buffer.BlockCopy(lenText, 0, rec, 0, lenText.Length);
    Buffer.BlockCopy(body, 0, rec, lenText.Length, body.Length);
    return rec;
  }
  void Pad(long len) {
    int r = (int)(len % 512);
    if (r != 0) Emit(zero, 0, 512 - r);
  }
  void ZeroFill(long n) {
    while (n > 0) {
      int k = (int)Math.Min(512L, n);
      Emit(zero, 0, k);
      n -= k;
    }
  }
  void WriteExtended(string name, byte[] records, long mtime) {
    Emit(Header("PaxHeaders/" + AsciiName(Path.GetFileName(name)), records.Length, mtime, (byte)'x'), 0, 512);
    Emit(records, 0, records.Length);
    Pad(records.Length);
  }
  // Returns the number of bytes zero-filled because the source ended early (truncated) or hit a Win32 byte-range lock in hot mode.
  // A non-zero result means the entry is not a faithful copy and must be superseded by the stopped delta.
  public long AddFile(string name, Stream data, long length, long mtimeUnix) {
    return AddFile(name, data, length, mtimeUnix, false);
  }
  public long AddFile(string name, Stream data, long length, long mtimeUnix, bool allowReadLock) {
    if (name.Length == 0 || name.StartsWith("/") || name.Contains("\\")) throw new ArgumentException("Invalid archive name: " + name);
    if (mtimeUnix < 0) mtimeUnix = 0;
    if (mtimeUnix > MaxTarTime) mtimeUnix = MaxTarTime;
    byte[] nb = Encoding.UTF8.GetBytes(name);
    bool big = length >= 8589934592L;
    if (nb.Length > 100 || HasNonAscii(name) || big) {
      MemoryStream recs = new MemoryStream();
      if (nb.Length > 100 || HasNonAscii(name)) { byte[] r = PaxRecord("path", name); recs.Write(r, 0, r.Length); }
      if (big) { byte[] r = PaxRecord("size", length.ToString(CultureInfo.InvariantCulture)); recs.Write(r, 0, r.Length); }
      WriteExtended(name, recs.ToArray(), mtimeUnix);
    }
    Emit(Header(AsciiName(name), big ? 0 : length, mtimeUnix, (byte)'0'), 0, 512);
    long left = length;
    long padded = 0;
    while (left > 0) {
      int n;
      try {
        n = data.Read(copyBuf, 0, (int)Math.Min((long)copyBuf.Length, left));
      } catch (IOException ex) {
        int hr = Marshal.GetHRForException(ex);
        if (allowReadLock && (hr == unchecked((int)0x80070021) || hr == unchecked((int)0x80070020))) {
          padded = left;
          ZeroFill(left);
          left = 0;
          break;
        }
        throw;
      }
      if (n <= 0) { padded = left; ZeroFill(left); left = 0; break; }
      Emit(copyBuf, 0, n);
      left -= n;
    }
    Pad(length);
    Entries++;
    return padded;
  }
  public long AddBytes(string name, byte[] data, long mtimeUnix) {
    return AddFile(name, new MemoryStream(data), data.Length, mtimeUnix);
  }
  public void Finish() {
    ZeroFill(1024);
    hashing.FlushFinalBlock();
    Sha256 = BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
    output.Flush();
  }
  public void Dispose() { hashing.Dispose(); }
}
public class NexusPaxScanEntry { public string FullName; public long Length; public long WriteTicks; }
public class NexusPaxReader {
  static bool ReadFull(Stream s, byte[] b) {
    int off = 0;
    while (off < b.Length) {
      int n = s.Read(b, off, b.Length - off);
      if (n <= 0) { if (off == 0) return false; throw new IOException("Truncated tar"); }
      off += n;
    }
    return true;
  }
  static string Text(byte[] h, int off, int len) {
    int n = 0;
    while (n < len && h[off + n] != 0) n++;
    return Encoding.UTF8.GetString(h, off, n);
  }
  static long Octal(byte[] h, int off, int len) {
    long v = 0;
    for (int i = off; i < off + len; i++) {
      byte c = h[i];
      if (c == 0 || c == 32) continue;
      if (c < 48 || c > 55) throw new IOException("Bad octal field");
      v = v * 8 + (c - 48);
    }
    return v;
  }
  static void Check(byte[] h) {
    long stored = Octal(h, 148, 8);
    long sum = 0;
    for (int i = 0; i < 512; i++) sum += (i >= 148 && i < 156) ? 32 : (int)h[i];
    if (sum != stored) throw new IOException("Tar header checksum mismatch");
  }
  static void Records(byte[] data, ref string path, ref long size) {
    int p = 0;
    while (p < data.Length) {
      int sp = p;
      while (sp < data.Length && data[sp] != 32) sp++;
      int len = int.Parse(Encoding.ASCII.GetString(data, p, sp - p), CultureInfo.InvariantCulture);
      string rec = Encoding.UTF8.GetString(data, sp + 1, len - (sp - p) - 1).TrimEnd('\n');
      int eq = rec.IndexOf('=');
      string key = rec.Substring(0, eq);
      string val = rec.Substring(eq + 1);
      if (key == "path") path = val;
      else if (key == "size") size = long.Parse(val, CultureInfo.InvariantCulture);
      p += len;
    }
  }
  public static ArrayList List(string file) {
    ArrayList list = new ArrayList();
    using (FileStream fs = new FileStream(file, FileMode.Open, FileAccess.Read)) {
      byte[] h = new byte[512];
      string pendPath = null;
      long pendSize = -1;
      while (true) {
        if (!ReadFull(fs, h)) throw new IOException("Missing end of archive");
        bool zero = true;
        foreach (byte b in h) if (b != 0) { zero = false; break; }
        if (zero) break;
        Check(h);
        string name = Text(h, 0, 100);
        long size = Octal(h, 124, 12);
        long mtime = Octal(h, 136, 12);
        byte type = h[156];
        if (type == (byte)'x') {
          byte[] data = new byte[size];
          if (!ReadFull(fs, data)) throw new IOException("Truncated pax header");
          Records(data, ref pendPath, ref pendSize);
          fs.Seek((512 - size % 512) % 512, SeekOrigin.Current);
          continue;
        }
        if (pendPath != null) name = pendPath;
        if (pendSize >= 0) size = pendSize;
        pendPath = null;
        pendSize = -1;
        if (type == (byte)'0' || type == 0) {
          Hashtable e = new Hashtable();
          e["Name"] = name;
          e["Size"] = size;
          e["Mtime"] = mtime;
          list.Add(e);
        }
        fs.Seek(size + (512 - size % 512) % 512, SeekOrigin.Current);
      }
    }
    return list;
  }
}
public class NexusPaxArchiver {
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern SafeFileHandle CreateFileW(string p, uint access, uint share, IntPtr sa, uint mode, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool GetFileTime(SafeFileHandle h, out long c, out long a, out long w);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct FindData {
    public uint attr;
    public System.Runtime.InteropServices.ComTypes.FILETIME creation, access, write;
    public uint high, low, reserved0, reserved1;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string name;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)] public string alt;
  }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern IntPtr FindFirstFileW(string path, out FindData data);
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern bool FindNextFileW(IntPtr handle, out FindData data);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool FindClose(IntPtr handle);
  static string Extended(string p) {
    if (p.StartsWith(@"\\?\")) return p;
    if (p.StartsWith(@"\\")) return @"\\?\UNC\" + p.Substring(2);
    return @"\\?\" + p;
  }
  static FileStream OpenRead(string path) {
    SafeFileHandle h = CreateFileW(Extended(path), 0x80000000, 0x7, IntPtr.Zero, 3, 0x80, IntPtr.Zero);
    if (h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "Open failed: " + path);
    return new FileStream(h, FileAccess.Read, 1 << 16, false);
  }
  static long WriteTicks(FileStream fs) {
    long c, a, w;
    if (!GetFileTime(fs.SafeFileHandle, out c, out a, out w)) throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFileTime failed");
    return w;
  }
  static long UnixSeconds(long writeTicks) {
    return (DateTime.FromFileTimeUtc(writeTicks) - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).Ticks / 10000000L;
  }
  static string Rel(string root, string full) {
    return full.Substring(root.TrimEnd('\\').Length + 1).Replace('\\', '/');
  }
  // Same traversal rules as NexusLongPaths.Scan in nexus-backup.ps1: no reparse points, long paths allowed.
  public static ArrayList Scan(string root) { return Scan(root, 0); }
  public static ArrayList Scan(string root, long deadlineTicks) {
    if (deadlineTicks > 0 && DateTime.UtcNow.Ticks > deadlineTicks) throw new IOException("Stopped scan deadline exceeded");
    ArrayList result = new ArrayList();
    Stack stack = new Stack();
    stack.Push(root.TrimEnd('\\'));
    int scanned = 0;
    while (stack.Count > 0) {
      if (deadlineTicks > 0 && (++scanned % 256) == 0 && DateTime.UtcNow.Ticks > deadlineTicks) throw new IOException("Stopped scan deadline exceeded");
      string dir = (string)stack.Pop();
      FindData d;
      IntPtr h = FindFirstFileW(Extended(dir + @"\*"), out d);
      if (h == new IntPtr(-1)) {
        int e = Marshal.GetLastWin32Error();
        if (e == 2) continue;
        throw new Win32Exception(e, "Path operation failed: " + dir);
      }
      try {
        do {
          if (deadlineTicks > 0 && (++scanned % 1024) == 0 && DateTime.UtcNow.Ticks > deadlineTicks) throw new IOException("Stopped scan deadline exceeded");
          if (d.name == "." || d.name == "..") continue;
          string path = dir + @"\" + d.name;
          if ((d.attr & 1024) != 0) throw new InvalidOperationException("Reparse point rejected: " + path);
          if ((d.attr & 16) != 0) { stack.Push(path); continue; }
          NexusPaxScanEntry entry = new NexusPaxScanEntry();
          entry.FullName = path;
          entry.Length = ((long)d.high << 32) | d.low;
          entry.WriteTicks = (((long)d.write.dwHighDateTime) << 32) | (long)(uint)d.write.dwLowDateTime;
          result.Add(entry);
        } while (FindNextFileW(h, out d));
        int error = Marshal.GetLastWin32Error();
        if (error != 18) throw new Win32Exception(error, "Enumeration failed: " + dir);
      } finally {
        FindClose(h);
      }
    }
    return result;
  }
  public static ArrayList ListFiles(string root) {
    ArrayList names = new ArrayList();
    foreach (NexusPaxScanEntry e in Scan(root)) names.Add(Rel(root, e.FullName));
    return names;
  }
  // Archives the named files (relative, '/' separated).
  // Deferred (no snapshot record, listed in locked): open-time sharing/lock violations, hot-read byte-range locks, or files truncated/changed while reading.
  // Hot pass (allowTransient): a file that vanished (Win32 2/3) is skipped; the stopped delta sees the removal.
  // Stopped pass: a vanished or read-locked file is an error because the stopped snapshot must be complete.
  public static Hashtable ArchiveNames(string root, string prefix, NexusPaxWriter w, IList names, IList locked, bool allowTransient) {
    Hashtable snapshot = new Hashtable();
    foreach (string rel in names) {
      string path = Path.Combine(root, rel.Replace('/', '\\'));
      FileStream fs;
      try {
        fs = OpenRead(path);
      } catch (Win32Exception ex) {
        int code = ex.NativeErrorCode;
        if (code == 32 || code == 33) { locked.Add(rel); continue; }
        if (allowTransient && (code == 2 || code == 3)) continue;
        throw;
      }
      using (fs) {
        long length = fs.Length;
        long ticks = WriteTicks(fs);
        long padded = w.AddFile(prefix + rel, fs, length, UnixSeconds(ticks), allowTransient);
        if (padded > 0 || WriteTicks(fs) != ticks || fs.Length != length) { locked.Add(rel); continue; }
        snapshot[rel] = new long[] { length, ticks };
      }
    }
    return snapshot;
  }
  // Compares a stopped-time scan with the hot snapshot. changed = new + modified + forced (deferred, then present).
  public static void Delta(string root, Hashtable before, IList forced, IList changed, IList deleted, long deadlineTicks) {
    if (deadlineTicks > 0 && DateTime.UtcNow.Ticks > deadlineTicks) throw new IOException("Stopped scan deadline exceeded");
    Hashtable seen = new Hashtable();
    int n = 0;
    foreach (NexusPaxScanEntry e in Scan(root, deadlineTicks)) {
      if (deadlineTicks > 0 && (++n % 1024) == 0 && DateTime.UtcNow.Ticks > deadlineTicks) throw new IOException("Stopped scan deadline exceeded");
      string rel = Rel(root, e.FullName);
      seen[rel] = true;
      long[] old = before[rel] as long[];
      if (old == null || old[0] != e.Length || old[1] != e.WriteTicks) changed.Add(rel);
    }
    foreach (object k in before.Keys) if (!seen.ContainsKey(k)) deleted.Add(k);
    foreach (string f in forced) {
      if (seen.ContainsKey(f)) {
        if (!changed.Contains(f)) changed.Add(f);
      } else if (!deleted.Contains(f)) {
        deleted.Add(f);
      }
    }
  }
}
'@
}
function Write-NexusArchive([string]$root,[string]$path,[string]$prefix,$names,[long]$deadlineTicks,[bool]$hot){
  $fs=New-Object IO.FileStream -ArgumentList $path,([IO.FileMode]::CreateNew),([IO.FileAccess]::Write),([IO.FileShare]::None),1048576
  $w=$null
  try{
    $w=New-Object NexusPaxWriter -ArgumentList $fs
    $w.DeadlineTicks=$deadlineTicks
    $locked=New-Object Collections.ArrayList
    $snapshot=[NexusPaxArchiver]::ArchiveNames($root,$prefix,$w,$names,$locked,$hot)
    $w.Finish()
    return @{Name=[IO.Path]::GetFileName($path);Bytes=$w.Bytes;Sha256=$w.Sha256;Entries=$w.Entries;Snapshot=$snapshot;Locked=$locked}
  }finally{
    if($w){$w.Dispose()}
    $fs.Dispose()
  }
}
function Get-NexusSha256([string]$path){
  $sha=New-Object Security.Cryptography.SHA256Managed
  $fs=[IO.File]::OpenRead($path)
  try{return ([BitConverter]::ToString($sha.ComputeHash($fs))).Replace('-','').ToLowerInvariant()}finally{$fs.Dispose()}
}
function Get-NexusReceiptNames($receipt){
  # Whitelist of files a pax-tar-v2 receipt owns. Receipts without Format are legacy and return $null.
  if(-not $receipt.Format){return $null}
  if($receipt.Format -ne 'pax-tar-v2'){throw ('Unknown receipt format: '+$receipt.Format)}
  $names=@('backup-result.json')
  foreach($a in @($receipt.Archives)){
    if($a.Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $a.Name -eq 'backup-result.json'){throw ('Unsafe archive name in receipt: '+$a.Name)}
    $names+=$a.Name
  }
  return $names
}
function Test-NexusArchiveSizes([string]$root,$archives){
  foreach($a in @($archives)){
    $p=Join-Path $root $a.Name
    if(-not [IO.File]::Exists($p)){throw ('Archive missing: '+$a.Name)}
    if(([IO.FileInfo]$p).Length -ne [long]$a.Bytes){throw ('Archive size mismatch: '+$a.Name)}
  }
}
