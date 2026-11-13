using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace GameQaKit.Unity
{
    public sealed class Command
    {
        public int Protocol, Frames;
        public long AfterFrame;
        public string Session, Id, PauseId, Action, Direction, Button;
        public int X, Y;
    }
    public sealed class Receipt
    {
        public Command Request;
        public bool Ok, Consumed;
        public string Error, Screenshot;
        public long Frame, CaptureFrame;
    }
    public sealed class ControlCore
    {
        public readonly string Session;
        public long Frame { get; private set; }
        public bool Paused { get; private set; }
        public string PauseId { get; private set; }
        public Receipt PendingReceipt { get; private set; }
        public string Error { get; private set; }
        private readonly HashSet<string> seen = new HashSet<string>();
        private readonly HashSet<string> held = new HashSet<string>();
        private readonly HashSet<string> previous = new HashSet<string>();
        private readonly HashSet<string> awaitingRelease = new HashSet<string>();
        public long ReleaseFrame { get; private set; }
        public bool ReleaseConfirmed { get { return ReleaseFrame>0 && awaitingRelease.Count==0 && held.Count==0; } }
        public void ObserveReleased(string token)
        {
            if(token!=null && !Held(token) && awaitingRelease.Remove(token) && awaitingRelease.Count==0)
                ReleaseFrame=Frame;
        }
        private Command active;
        private string acceptedPause="";
        private int remaining, settle;
        private bool consumed;
        public string ActiveId { get { return active==null ? null : active.Id; } }
        public Command Active { get { return active; } }
        public int HeldCount { get { return held.Count; } }
        public string[] HeldButtons { get { var items=new string[held.Count]; held.CopyTo(items); return items; } }
        public ControlCore(string session) { Session=session; Paused=true; PauseId=""; }
        public bool Held(string key) { return held.Contains(key); }
        public bool Down(string key) { return Held(key) && !previous.Contains(key); }
        public bool Up(string key) { return !Held(key) && previous.Contains(key); }
        public void BeginFrame(double now, double leaseExpiry, string pauseId)
        {
            Frame++;
            previous.Clear(); previous.UnionWith(held);
            if(pauseId!=null && pauseId!=PauseId) { PauseId=pauseId; Fail("manual_pause"); }
            if(!Paused && (!Finite(now) || !Finite(leaseExpiry) || leaseExpiry<now)) Fail("lease_expired");
            if(active!=null && remaining==0) held.Clear();
        }
        public static bool Finite(double number) { return !double.IsNaN(number) && !double.IsInfinity(number); }
        private static bool ButtonValid(string button)
        {
            return Array.IndexOf(new[]{"up","down","left","right","ok","cancel","shift","pageup","pagedown"},button)>=0;
        }
        public bool Accept(Command command, double now, double leaseExpiry)
        {
            if(command==null || command.Session!=Session || command.Protocol!=1 || command.Id==null ||
                !Regex.IsMatch(command.Id,@"\A[A-Za-z0-9_-]{1,64}\z") || seen.Contains(command.Id)) return false;
            if(active!=null || PendingReceipt!=null) return false;
            seen.Add(command.Id);
            active=command; consumed=false;
            string action=command.Action;
            bool valid=command.Frames>=1 && command.Frames<=120 && command.AfterFrame>=0 && command.AfterFrame<=Frame &&
                Array.IndexOf(new[]{"resume","move","confirm","cancel","press","release","wait","click"},action)>=0;
            if(action=="move") valid=valid && Array.IndexOf(new[]{"up","down","left","right"},command.Direction)>=0;
            if(action=="press") valid=valid && ButtonValid(command.Button);
            if(action=="release") valid=valid && (command.Button==null || ButtonValid(command.Button));
            if(action=="click") valid=valid && command.X>=0 && command.Y>=0;
            if(!valid) { Fail("invalid_command"); return false; }
            if(command.PauseId!=PauseId || !Finite(leaseExpiry) || leaseExpiry<now)
            { Fail("stale_command"); return false; }
            if(action=="resume") { Paused=false; acceptedPause=PauseId; Error=null; }
            else if(Paused || command.PauseId!=acceptedPause) { Fail("control_paused"); return false; }
            held.Clear();
            string token=action=="move" ? command.Direction : action=="confirm" ? "ok" :
                action=="cancel" ? "cancel" : action=="press" ? command.Button : action=="click" ? "mouse0" : null;
            if(token!=null) { held.Add(token); awaitingRelease.Add(token); ReleaseFrame=0; }
            remaining=command.Frames; settle=2;
            consumed=token==null;
            return true;
        }
        public void Observe(string token)
        {
            if(active==null) return;
            string wanted=active.Action=="move" ? active.Direction : active.Action=="confirm" ? "ok" :
                active.Action=="cancel" ? "cancel" : active.Action=="press" ? active.Button : active.Action=="click" ? "mouse0" : null;
            if(token==wanted && token!=null) consumed=true;
        }
        public bool EndFrame()
        {
            if(active==null) return false;
            if(remaining>0) { remaining--; return false; }
            if(settle>0) settle--;
            if(settle!=0) return false;
            if(!consumed) { Fail("input_not_observed"); return false; }
            return true;
        }
        public void Captured(string screenshot) { Captured(screenshot,Frame); }
        public void Captured(string screenshot,long captureFrame)
        {
            if(active==null) throw new InvalidOperationException("No active capture");
            if(captureFrame<=active.AfterFrame || captureFrame>Frame) throw new ArgumentOutOfRangeException("captureFrame");
            PendingReceipt=new Receipt { Request=active, Ok=true, Consumed=consumed,
                Frame=captureFrame, CaptureFrame=captureFrame, Screenshot=screenshot };
            held.Clear(); active=null;
        }
        public void Fail(string error)
        {
            Error=error; Paused=true; held.Clear();
            if(active!=null) PendingReceipt=new Receipt { Request=active, Ok=false,
                Consumed=consumed, Frame=Frame, CaptureFrame=Frame, Error=error };
            active=null;
        }
        public void AcknowledgeReceipt() { PendingReceipt=null; }
    }
    public static class Wire
    {
        public static Dictionary<string,string> ReadIni(string path)
        {
            var values=new Dictionary<string,string>(StringComparer.Ordinal);
            if(!File.Exists(path)) return values;
            if(new FileInfo(path).Length>65536) throw new FormatException("Control file exceeds 64 KiB");
            string text;
            using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))
            using(var reader=new StreamReader(stream,Encoding.UTF8,true)) text=reader.ReadToEnd();
            bool section=false;
            foreach(string line in text.Split('\n'))
            {
                string value=line.Trim();
                if(value.Length==0 || value.StartsWith(";") || value.StartsWith("#")) continue;
                if(value=="[bridge]") { if(section) throw new FormatException("Duplicate section"); section=true; continue; }
                int equal=value.IndexOf('=');
                if(!section || equal<1) throw new FormatException("Invalid bridge INI");
                string key=value.Substring(0,equal).Trim();
                if(values.ContainsKey(key)) throw new FormatException("Duplicate control field");
                values.Add(key,value.Substring(equal+1).Trim());
            }
            return values;
        }
        public static string Get(Dictionary<string,string> values,string key,string fallback="")
        { string value; return values.TryGetValue(key,out value) ? value : fallback; }
        public static string PauseId(Dictionary<string,string> values,string session)
        { return Get(values,"session_id")==session ? Get(values,"id") : null; }
        public static double Number(Dictionary<string,string> values,string key)
        { return double.Parse(Get(values,key),NumberStyles.Float,CultureInfo.InvariantCulture); }
        public static Command ParseCommand(Dictionary<string,string> values)
        {
            string action=Get(values,"payload.action");
            var allowed=new HashSet<string>(new[]{"protocol","session_id","id","after_frame","pause_id","created_at","payload.action","payload.frames"});
            if(action=="move") allowed.Add("payload.direction");
            if(action=="press" || action=="release") allowed.Add("payload.button");
            if(action=="click") { allowed.Add("payload.x"); allowed.Add("payload.y"); }
            foreach(string key in values.Keys) if(!allowed.Contains(key)) throw new FormatException("Unknown command field: "+key);
            return new Command { Protocol=int.Parse(Get(values,"protocol"),CultureInfo.InvariantCulture),
                Session=Get(values,"session_id"), Id=Get(values,"id"), PauseId=Get(values,"pause_id"),
                AfterFrame=long.Parse(Get(values,"after_frame"),CultureInfo.InvariantCulture), Action=action,
                Frames=int.Parse(Get(values,"payload.frames"),CultureInfo.InvariantCulture),
                Direction=Get(values,"payload.direction"), Button=Get(values,"payload.button",null),
                X=action=="click" ? int.Parse(Get(values,"payload.x"),CultureInfo.InvariantCulture) : 0,
                Y=action=="click" ? int.Parse(Get(values,"payload.y"),CultureInfo.InvariantCulture) : 0 };
        }
        public static void Write(string path,byte[] data)
        {
            string temporary=path+".part";
            File.WriteAllBytes(temporary,data);
            for(int attempt=0;;attempt++)
            {
                try
                {
                    if(File.Exists(path)) File.Replace(temporary,path,null); else File.Move(temporary,path);
                    return;
                }
                catch(IOException error)
                {
                    int code=System.Runtime.InteropServices.Marshal.GetHRForException(error)&0xffff;
                    if(Environment.OSVersion.Platform!=PlatformID.Win32NT || (code!=5 && code!=32 && code!=33) || attempt>=7) throw;
                    System.Threading.Thread.Sleep(1);
                }
            }
        }
        public static void WriteState(string path,object value)
        {
            byte[] data=new UTF8Encoding(false).GetBytes(Json(value));
            for(int attempt=0;;attempt++)
            {
                try
                {
                    using(var stream=new FileStream(path,FileMode.OpenOrCreate,FileAccess.Write,FileShare.None))
                    {
                        stream.SetLength(data.Length);
                        stream.Write(data,0,data.Length);
                        stream.Flush();
                    }
                    return;
                }
                catch(IOException error)
                {
                    int code=System.Runtime.InteropServices.Marshal.GetHRForException(error)&0xffff;
                    if(Environment.OSVersion.Platform!=PlatformID.Win32NT || (code!=5 && code!=32 && code!=33) || attempt>=7) throw;
                    System.Threading.Thread.Sleep(1);
                }
            }
        }
        public static void WriteJson(string path,object value) { Write(path,new UTF8Encoding(false).GetBytes(Json(value))); }
        public static string Json(object value)
        {
            if(value==null) return "null";
            if(value is string)
            {
                var output=new StringBuilder("\"");
                foreach(char c in (string)value)
                {
                    switch(c) { case '\\': output.Append("\\\\"); break; case '"': output.Append("\\\""); break;
                        case '\n': output.Append("\\n"); break; case '\r': output.Append("\\r"); break;
                        case '\t': output.Append("\\t"); break;
                        default: if(c<32) output.Append("\\u"+((int)c).ToString("x4")); else output.Append(c); break; }
                }
                return output.Append('"').ToString();
            }
            if(value is bool) return (bool)value ? "true" : "false";
            var dictionary=value as IDictionary;
            if(dictionary!=null)
            {
                var parts=new List<string>();
                foreach(DictionaryEntry item in dictionary) parts.Add(Json((string)item.Key)+":"+Json(item.Value));
                return "{"+string.Join(",",parts.ToArray())+"}";
            }
            var list=value as IEnumerable;
            if(list!=null)
            {
                var parts=new List<string>(); foreach(object item in list) parts.Add(Json(item));
                return "["+string.Join(",",parts.ToArray())+"]";
            }
            if(value is IConvertible)
            {
                double number=Convert.ToDouble(value,CultureInfo.InvariantCulture);
                if(!ControlCore.Finite(number)) throw new FormatException("Non-finite JSON number");
                return Convert.ToString(value,CultureInfo.InvariantCulture);
            }
            throw new FormatException("Unsupported JSON value");
        }
    }
}
