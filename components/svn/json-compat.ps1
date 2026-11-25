# Dependency-free JSON for PowerShell 2 / .NET 2+. No System.Web.Extensions.
if (-not ('CoreNasJson' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections;
using System.Globalization;
using System.Text;
public class CoreNasJson {
 string s; int i;
 public object DeserializeObject(string text) {
  s=text;i=0;object v=Read(0);Space();if(i!=s.Length)throw new FormatException("Trailing JSON");return v;
 }
 void Space(){while(i<s.Length && char.IsWhiteSpace(s[i]))i++;}
 char Take(){if(i>=s.Length)throw new FormatException("Truncated JSON");return s[i++];}
 object Read(int depth){
  if(depth>64)throw new FormatException("JSON too deep");Space();if(i>=s.Length)throw new FormatException("Empty JSON");char c=s[i];
  if(c=='"')return Str();
  if(c=='{'){i++;Hashtable h=new Hashtable();Space();if(i<s.Length && s[i]=='}'){i++;return h;}
   while(true){Space();if(i>=s.Length || s[i]!='"')throw new FormatException("Object key expected");string k=Str();Space();if(Take()!=':')throw new FormatException("Colon expected");if(h.ContainsKey(k))throw new FormatException("Duplicate JSON key");h.Add(k,Read(depth+1));Space();c=Take();if(c=='}')return h;if(c!=',')throw new FormatException("Comma expected");}}
  if(c=='['){i++;ArrayList a=new ArrayList();Space();if(i<s.Length && s[i]==']'){i++;return a.ToArray();}
   while(true){a.Add(Read(depth+1));Space();c=Take();if(c==']')return a.ToArray();if(c!=',')throw new FormatException("Comma expected");}}
  int start=i;while(i<s.Length && !char.IsWhiteSpace(s[i]) && ",]}".IndexOf(s[i])<0)i++;
  string t=s.Substring(start,i-start);if(t=="true")return true;if(t=="false")return false;if(t=="null")return null;
  if(!System.Text.RegularExpressions.Regex.IsMatch(t,@"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$"))throw new FormatException("Invalid JSON token");
  return decimal.Parse(t,NumberStyles.Float,CultureInfo.InvariantCulture);
 }
 string Str(){
  if(Take()!='"')throw new FormatException("String expected");StringBuilder b=new StringBuilder();
  while(true){char c=Take();if(c=='"')return b.ToString();if(c<32)throw new FormatException("Control character");
   if(c!='\\'){b.Append(c);continue;}c=Take();
   switch(c){case '"':case '\\':case '/':b.Append(c);break;case 'b':b.Append('\b');break;case 'f':b.Append('\f');break;case 'n':b.Append('\n');break;case 'r':b.Append('\r');break;case 't':b.Append('\t');break;
   case 'u':if(i+4>s.Length)throw new FormatException("Bad Unicode escape");b.Append((char)int.Parse(s.Substring(i,4),NumberStyles.HexNumber));i+=4;break;default:throw new FormatException("Invalid escape");}}
 }
 public string Serialize(object value){StringBuilder b=new StringBuilder();Write(b,value,0);return b.ToString();}
 void Quote(StringBuilder b,string t){b.Append('"');foreach(char c in t){switch(c){case '"':b.Append("\\\"");break;case '\\':b.Append("\\\\");break;case '\n':b.Append("\\n");break;case '\r':b.Append("\\r");break;case '\t':b.Append("\\t");break;default:if(c<32)b.Append("\\u"+((int)c).ToString("x4"));else b.Append(c);break;}}b.Append('"');}
 void Write(StringBuilder b,object v,int depth){
  if(depth>64)throw new FormatException("JSON too deep");
  // PowerShell 2 may wrap method arguments and dictionary values in PSObject.
  for(int wrap=0; v!=null && v.GetType().FullName=="System.Management.Automation.PSObject"; wrap++){
   if(wrap>16)throw new FormatException("Recursive PSObject wrapper");
   object unwrapped=v.GetType().GetProperty("BaseObject").GetValue(v,null);
   if(Object.ReferenceEquals(v,unwrapped))throw new FormatException("Unsupported custom PSObject");
   v=unwrapped;
  }
  if(v==null){b.Append("null");return;}
  if(v is string || v is char){Quote(b,v.ToString());return;}if(v is bool){b.Append((bool)v?"true":"false");return;}
  if(v is IDictionary){b.Append('{');bool first=true;foreach(DictionaryEntry e in (IDictionary)v){if(!first)b.Append(',');first=false;Quote(b,Convert.ToString(e.Key,CultureInfo.InvariantCulture));b.Append(':');Write(b,e.Value,depth+1);}b.Append('}');return;}
  if(v is IEnumerable){b.Append('[');bool first=true;foreach(object x in (IEnumerable)v){if(!first)b.Append(',');first=false;Write(b,x,depth+1);}b.Append(']');return;}
  TypeCode code=Type.GetTypeCode(v.GetType());if(code>=TypeCode.SByte && code<=TypeCode.Decimal){b.Append(Convert.ToString(v,CultureInfo.InvariantCulture));return;}
  throw new FormatException("Unsupported JSON value type: "+v.GetType().FullName);
 }
}
'@
}
$json=New-Object CoreNasJson
