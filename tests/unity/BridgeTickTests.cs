using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using GameQaKit.Unity;

// Only Unity rendering and input services are replaced. Tick and Lease are compiled
// directly from GameQaBridge.cs; file reads and command acceptance remain real.
namespace UnityEngine
{
    internal static class Input { internal static void ResetInputAxes() {} }
    internal static class Time { internal static float unscaledDeltaTime=1f/60; }
    internal static class Screen { internal static int width=320,height=240; }
}

internal sealed partial class BridgeTickHarness
{
    private readonly string directory;
    private readonly string session="test-session";
    internal readonly ControlCore core=new ControlCore("test-session");
    internal readonly List<Receipt> receipts=new List<Receipt>();
    internal int statusWrites;
    internal void BeginPublication() { publication=new CapturePublisher.Publication(null,core.Frame,9); }
    internal Action<string> afterRead;
    private readonly FrameRouter router=new FrameRouter();
    private readonly CapturePublisher publisher=new CapturePublisher();
    private CapturePublisher.Publication publication=null;
    private string observationError=null;
    private void FailCapture(Exception error) { core.Fail("capture_failed: "+error.Message); TryReport(); }
    private IDisposable Trace(string phase) { return null; }
    private static double Now { get { return 10; } }
    private static double CaptureClock { get { return Now; } }
    private bool Ready { get { return true; } }
    private sealed class FrameRouter
    {
        internal bool DrivesFrames { get { return false; } }
        internal void Advance(float delta) {}
    }
    internal BridgeTickHarness(string root)
    {
        directory=Path.Combine(root,Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        Write("pause","id=stop\nsession_id=test-session\n");
        Write("lease","session_id=test-session\nexpires_at=11\n");
        core.BeginFrame(10,11,"stop");
    }
    internal void Write(string name,string content)
    { Wire.Write(Path.Combine(directory,name+".ini"),Encoding.UTF8.GetBytes("[bridge]\n"+content)); }
    private Dictionary<string,string> Read(string name)
    {
        var result=Wire.ReadIni(Path.Combine(directory,name+".ini"));
        var callback=afterRead;
        if(callback!=null) callback(name);
        return result;
    }
    internal void Request(string id,string action,string pause)
    {
        Write("request","protocol=1\nsession_id=test-session\nid="+id+"\nafter_frame=0\npause_id="+pause+
            "\ncreated_at=10\npayload.action="+action+"\npayload.frames=1\n"+
            (action=="move" ? "payload.direction=right\n" : ""));
    }
    internal void StartRunning()
    {
        core.Accept(new Command { Protocol=1,Session=session,Id="warmup",PauseId="stop",
            Action="resume",Frames=1,AfterFrame=0 },10,11);
        core.Captured("screenshots/warmup.png");
        core.AcknowledgeReceipt();
    }
    private void FlushReceipt()
    {
        if(core.PendingReceipt==null) return;
        receipts.Add(core.PendingReceipt);
        core.AcknowledgeReceipt();
    }
    private void WriteStatus(bool force) { statusWrites++; }
    private void TryReport() { FlushReceipt(); }
    internal void Step() { Tick(); }
}

internal static class BridgeTickTests
{
    private static int checks,failures;
    private static void Check(bool condition,string message)
    { checks++; if(!condition) throw new Exception(message); }
    private static void Case(string name,Action test)
    {
        try { test(); Console.WriteLine("PASS: "+name); }
        catch(Exception error) { failures++; Console.Error.WriteLine("FAIL: "+name+": "+error.Message); }
    }
    public static int Main(string[] args)
    {
        Case("resume published during frame intake",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]);
            bridge.Write("lease","session_id=test-session\nexpires_at=9\n");
            bridge.afterRead=delegate(string name)
            {
                if(name!="lease") return;
                bridge.afterRead=null;
                // Host publication order is lease first, then request. The bridge
                // must not pair that new request with an earlier expired lease.
                bridge.Write("lease","session_id=test-session\nexpires_at=11\n");
                bridge.Request("fresh-resume","resume","stop");
            };
            bridge.Step(); bridge.Step();
            Check(bridge.receipts.Count==0,"fresh resume was rejected: "+bridge.core.Error);
            Check(!bridge.core.Paused && bridge.core.ActiveId=="fresh-resume","fresh resume must become active");
        });
        Case("pause published while reading a command wins",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]); bridge.StartRunning();
            bridge.Request("queued-move","move","stop");
            bridge.afterRead=delegate(string name)
            {
                if(name!="request") return;
                bridge.afterRead=null;
                bridge.Write("pause","session_id=test-session\nid=human-stop\n");
            };
            bridge.Step();
            Check(bridge.core.Paused && bridge.core.HeldCount==0,"new pause must preempt queued input");
            Check(bridge.receipts.Count==1 && !bridge.receipts[0].Ok && !bridge.receipts[0].Consumed,
                "queued input must be rejected without consumption");
        });
        Case("pending PNG still checks pause and heartbeat without accepting the next action",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]); bridge.StartRunning(); bridge.BeginPublication();
            bridge.Request("next-move","move","stop");
            bool readRequest=false;
            bridge.afterRead=delegate(string name) { if(name=="request") readRequest=true; };
            bridge.Step();
            Check(!readRequest && bridge.core.Active==null,"pending PNG must block the next command intake");
            Check(bridge.statusWrites==1,"pending publication must not suppress main-thread status");
            bridge.Write("pause","session_id=test-session\nid=human-stop\n");
            bridge.Step();
            Check(bridge.core.Paused && bridge.core.HeldCount==0 && bridge.statusWrites==2,
                "pending PNG must still confirm priority pause with fresh main-thread state");
        });
        Case("actually expired lease still refuses resume",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]);
            bridge.Write("lease","session_id=test-session\nexpires_at=9\n");
            bridge.Request("expired-resume","resume","stop"); bridge.Step();
            Check(bridge.core.Paused && bridge.core.HeldCount==0,"expired lease must leave input released");
            Check(bridge.receipts.Count==1 && bridge.receipts[0].Error=="stale_command" && !bridge.receipts[0].Consumed,
                "expired resume must fail closed");
        });
        Case("foreign lease cannot authorize resume",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]);
            bridge.Write("lease","session_id=old-session\nexpires_at=11\n");
            bridge.Request("foreign-lease","resume","stop"); bridge.Step();
            Check(bridge.core.Paused && bridge.receipts.Count==1 && !bridge.receipts[0].Ok,
                "another session's lease must not enable input");
        });
        Case("old pause generation cannot resume",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]);
            bridge.Request("old-generation","resume",""); bridge.Step();
            Check(bridge.core.Paused && bridge.receipts.Count==1 && !bridge.receipts[0].Consumed,
                "old pause generation must remain rejected");
        });
        Case("already published valid resume succeeds",delegate()
        {
            var bridge=new BridgeTickHarness(args[0]);
            bridge.Request("valid-resume","resume","stop"); bridge.Step();
            Check(!bridge.core.Paused && bridge.core.ActiveId=="valid-resume" && bridge.receipts.Count==0,
                "valid resume must become active");
        });
        Console.WriteLine((failures==0 ? "PASS: " : "FAIL: ")+checks+" Unity frame intake assertions; "+failures+" failures");
        return failures==0 ? 0 : 1;
    }
}
