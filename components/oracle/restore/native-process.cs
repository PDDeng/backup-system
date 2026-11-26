// .NET 2.0: keep Oracle stdout/stderr out of PowerShell's shared pipeline handles.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
public static class OracleRestoreNative {
 public static string Quote(string value) {
  StringBuilder b=new StringBuilder("\""); int slashes=0;
  foreach(char c in value) {
   if(c=='\\') {slashes++;continue;}
   if(c=='"') {b.Append('\\',slashes*2+1);b.Append(c);slashes=0;continue;}
   b.Append('\\',slashes);slashes=0;b.Append(c);
  }
  b.Append('\\',slashes*2);b.Append('"');return b.ToString();
 }
 public static int Run(string exe,string[] args,string log) {
  using(StreamWriter w=new StreamWriter(log,false,new UTF8Encoding(false)))
  using(Process p=new Process())
  using(ManualResetEvent outDone=new ManualResetEvent(false))
  using(ManualResetEvent errDone=new ManualResetEvent(false)) {
   object gate=new object(); Exception outputError=null;
   p.StartInfo.FileName=exe;p.StartInfo.UseShellExecute=false;p.StartInfo.CreateNoWindow=true;
   p.StartInfo.RedirectStandardOutput=true;p.StartInfo.RedirectStandardError=true;
   StringBuilder command=new StringBuilder();foreach(string a in args){if(command.Length>0)command.Append(' ');command.Append(Quote(a));}
   p.StartInfo.Arguments=command.ToString();
   p.OutputDataReceived+=delegate(object s,DataReceivedEventArgs e){if(e.Data==null){outDone.Set();return;} lock(gate){try{w.WriteLine(e.Data);w.Flush();}catch(Exception x){outputError=x;}}};
   p.ErrorDataReceived+=delegate(object s,DataReceivedEventArgs e){if(e.Data==null){errDone.Set();return;} lock(gate){try{w.WriteLine(e.Data);w.Flush();}catch(Exception x){outputError=x;}}};
   p.Start();p.BeginOutputReadLine();p.BeginErrorReadLine();
   // Do not abort server-side Oracle jobs by an arbitrary client timeout.
   p.WaitForExit();
   outDone.WaitOne();errDone.WaitOne();
   if(outputError!=null)throw new IOException("Native log write failed",outputError);
   return p.ExitCode;
  }
 }
}
