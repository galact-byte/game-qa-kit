using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.IO;
using GameQaKit.Bakin;

class ChannelTests
{
    static Dictionary<string, object> Message(string kind, int sequence, ulong now)
    {
        return new Dictionary<string, object> {
            {"protocol",2},{"bridge_version",3},{"session_id","session_1"},{"pid",12},{"creation_id","12345"},
            {"sequence",sequence},{"id","command_" + sequence},{"kind",kind},{"key",kind == "down" ? 16 : 0},
            {"expires_at_ms",(long)now + 500},{"duration_ms",kind == "down" ? 200 : 0},
            {"start_deadline_ms",kind == "down" ? (long)now + 500 : 0L},
            {"hard_stop_at_ms",kind == "down" ? (long)now + 700 : 0L}};
    }
    static Dictionary<string, object> Lease(ulong now)
    {
        return new Dictionary<string, object> {
            {"protocol",2},{"bridge_version",3},{"session_id","session_1"},{"pid",12},{"creation_id","12345"},
            {"expires_at_ms",(long)now + 1500}};
    }
    static ChannelState Start()
    {
        var s = new ChannelState("session_1", 12, "12345");
        s.Lease(Lease(1000), 1000); s.Apply(Message("resume",1,1000),1000);
        return s;
    }
    static void Equal(object want, object got) { if (!want.Equals(got)) throw new Exception("Expected " + want + ", got " + got); }
    static void Reject(Action action) { try { action(); } catch (ArgumentException) { return; } throw new Exception("Unsafe message accepted"); }
    static int Main(string[] args)
    {
        try
        {
            string name = args[0];
            if (name == "wire")
            {
                var json = new JavaScriptSerializer();
                var s = new ChannelState("session_1",12,"12345");
                s.Lease(json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(args[1],"target-lease.json"))),1000);
                foreach(string path in Directory.GetFiles(Path.Combine(args[1],"commands"),"*.json"))
                {
                    var message=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));
                    s.Apply(message,1000); s.Input.Sample(1000); s.Input.Query(0,0,1000);
                }
                Equal(3L,s.Sequence); Equal(false,s.Paused); Equal(0UL,s.Input.Held);
                Equal("action",s.Input.PressEvidence.ActionId); Equal("action",s.Input.ReleaseEvidence.ActionId);
            }
            else if (name == "identity")
            {
                foreach (string field in new[]{"protocol","bridge_version","session_id","pid","creation_id"})
                { var s=Start(); var m=Message("down",2,1000); m[field]=field == "session_id" || field == "creation_id" ? (object)"other" : true; Reject(()=>s.Apply(m,1000)); Equal(0UL,s.Input.Query(0,0,1000)); }
            }
            else if (name == "schema")
            {
                foreach (string field in new[]{"surprise","key","sequence","expires_at_ms","duration_ms","start_deadline_ms","hard_stop_at_ms"})
                { var s=Start(); var m=Message("down",2,1000); m[field]=true; Reject(()=>s.Apply(m,1000)); }
            }
            else if (name == "duplicate")
            { var s=Start(); Equal(true,s.Apply(Message("down",2,1000),1000)); s.Input.Sample(1000); Equal(16UL,s.Input.Query(0,0,1000)); Equal(false,s.Apply(Message("down",2,1100),1100)); s.Input.Sample(1201); Equal(0UL,s.Input.Query(0,0,1201)); }
            else if (name == "gap")
            { var s=Start(); Reject(()=>s.Apply(Message("down",3,1000),1000)); Equal(0UL,s.Input.Query(0,0,1000)); }
            else if (name == "expiry")
            { var s=Start(); Reject(()=>s.Apply(Message("down",2,500),1500)); }
            else if (name == "overflow")
            { foreach(object v in new object[]{-1L,Double.NaN,Double.PositiveInfinity,1.5,UInt64.MaxValue}) { var s=Start(); var m=Message("down",2,1000); m["duration_ms"]=v; Reject(()=>s.Apply(m,1000)); } }
            else if (name == "lease")
            { var s=Start(); s.Apply(Message("down",2,1000),1000); s.Input.Sample(1000); Equal(16UL,s.Input.Query(0,0,1000)); s.Tick(2501); s.Lease(Lease(2501),2501); Equal(true,s.Paused); Equal(0UL,s.Input.Query(0,0,2501)); Reject(()=>s.Apply(Message("down",3,2501),2501)); }
            else if (name == "pause")
            { var s=Start(); s.Apply(Message("down",2,1000),1000); s.Input.Sample(1000); Equal(16UL,s.Input.Query(0,0,1000)); s.Apply(Message("pause",3,1050),1050); Equal(0UL,s.Input.Query(0,0,1050)); Equal(true,s.Paused); }
            else if (name == "resume")
            { var s=Start(); s.Apply(Message("pause",2,1000),1000); s.Apply(Message("resume",3,1050),1050); s.Input.Sample(1050); Equal(0UL,s.Input.Query(0,0,1050)); s.Apply(Message("down",4,1050),1050); s.Input.Sample(1060); Equal(16UL,s.Input.Query(0,0,1060)); }
            else if (name == "bounds")
            { var s=Start(); var m=Message("down",2,1000); m["key"]=3; Reject(()=>s.Apply(m,1000)); s=Start(); m=Message("pause",2,1000); m["duration_ms"]=1200L; Reject(()=>s.Apply(m,1000)); }
            else if (name == "version")
            {
                foreach (int old in new[]{1, 2})
                {
                    var s=Start(); var m=Message("down",2,1000); m["bridge_version"]=old;
                    Reject(()=>s.Apply(m,1000)); Equal(0UL,s.Input.Query(0,0,1000));
                }
            }
            else if (name == "timing")
            {
                foreach(string field in new[]{"duration_ms","start_deadline_ms","hard_stop_at_ms"})
                {
                    var s=Start(); var m=Message("down",2,1000); m[field]=0L; Reject(()=>s.Apply(m,1000));
                }
                var valid=Start(); var cmd=Message("down",2,1000); valid.Apply(cmd,1100);
                valid.Input.Sample(1200); Equal(16UL,valid.Input.Query(0,0,1200));
                Equal(1400UL,valid.Input.PressEvidence.HoldUntil);
                valid.Input.Sample(1400); Equal(0UL,valid.Input.Query(0,0,1400));
            }
            else if (name == "legacy")
            {
                var s=Start(); var m=Message("down",2,1000); m["hold_until_ms"]=1200L;
                Reject(()=>s.Apply(m,1000));
            }
            else if (name == "fault")
            { var s=Start(); s.Fault("publish_failed"); Equal(true,s.Paused); s.Lease(Lease(1000),1000); Reject(()=>s.Apply(Message("resume",2,1000),1000)); Equal(0UL,s.Input.Query(0,0,1000)); }
            else throw new Exception("Unknown case");
            Console.WriteLine("PASS " + name); return 0;
        }
        catch(Exception e) { Console.Error.WriteLine(e); return 1; }
    }
}
