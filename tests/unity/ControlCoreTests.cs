using System;
using GameQaKit.Unity;

internal static class ControlCoreTests
{
    private static int checks;
    private static void Check(bool value, string message)
    {
        checks++;
        if (!value) throw new Exception(message);
    }
    private static Command Request(ControlCore core, string id, string action, int frames)
    {
        return new Command { Id=id, Session="test-session", Protocol=1, AfterFrame=core.Frame,
            PauseId=core.PauseId, Action=action, Frames=frames, Direction="right" };
    }
    private static ControlCore Running()
    {
        var core = new ControlCore("test-session");
        Check(core.Paused, "new sessions must start paused");
        core.BeginFrame(10, 11, "");
        Check(core.Accept(Request(core, "resume", "resume", 1), 10, 11), "resume accepted");
        Finish(core, false);
        Check(!core.Paused, "resume leaves AI active");
        return core;
    }
    private static void Finish(ControlCore core, bool read)
    {
        for (int i=0; i<8 && core.PendingReceipt==null; i++)
        {
            if (read) core.Observe("right");
            if (core.EndFrame()) core.Captured("screenshots/"+core.ActiveId+".png");
            if (core.PendingReceipt==null) core.BeginFrame(10, 11, core.PauseId);
        }
        Check(core.PendingReceipt!=null, "command terminates");
        Check(core.PendingReceipt.Ok, "command succeeds");
        core.AcknowledgeReceipt();
    }
    private static void AtomicPublication()
    {
        string directory=System.IO.Path.Combine(System.IO.Path.GetTempPath(),"gameqa-wire-"+Guid.NewGuid().ToString("N"));
        System.IO.Directory.CreateDirectory(directory);
        string path=System.IO.Path.Combine(directory,"status.json");
        Exception writerError=null;
        try
        {
            Wire.WriteState(path,"old");
            var writer=new System.Threading.Thread(delegate()
            {
                try { for(int i=0;i<200;i++) Wire.WriteState(path,(i%2==0)?"new":"old"); }
                catch(Exception error) { writerError=error; }
            });
            writer.Start();
            try
            {
                for(int i=0;i<400;i++)
                {
                    for(int attempt=0;;attempt++)
                    {
                        try
                        {
                            using(var stream=new System.IO.FileStream(path,System.IO.FileMode.Open,System.IO.FileAccess.Read,System.IO.FileShare.ReadWrite))
                            using(var reader=new System.IO.StreamReader(stream))
                            {
                                string text=reader.ReadToEnd();
                                if(text!="\"old\"" && text!="\"new\"") throw new Exception("Partial status publication: "+text);
                            }
                            break;
                        }
                        catch(System.IO.IOException error)
                        {
                            int code=System.Runtime.InteropServices.Marshal.GetHRForException(error)&0xffff;
                            if((code!=2 && code!=5 && code!=32 && code!=33) || attempt>=7) throw;
                            System.Threading.Thread.Sleep(1);
                        }
                    }
                }
            }
            finally { writer.Join(); }
            Check(writerError==null,"concurrent status publication must retain complete readable JSON: "+writerError);
        }
        finally { System.IO.Directory.Delete(directory,true); }
    }
    public static int Main()
    {
        try
        {
            AtomicPublication();
            var release=Running();
            Check(!release.ReleaseConfirmed,"an idle session is not evidence of target release consumption");
            release.Accept(Request(release,"release-witness","move",1),10,11);
            release.Observe("right");
            release.ObserveReleased("right");
            Check(!release.ReleaseConfirmed,"a query while held must not confirm release");
            release.EndFrame(); release.BeginFrame(10,11,"");
            Check(release.HeldCount==0 && !release.ReleaseConfirmed,"software clearing is not target release consumption");
            release.ObserveReleased("left");
            Check(!release.ReleaseConfirmed,"an unrelated release query cannot confirm the input");
            release.ObserveReleased("right");
            Check(release.ReleaseConfirmed && release.ReleaseFrame==release.Frame,"matching target release query confirms its real frame");
            Finish(release,false);
            release.Accept(Request(release,"release-witness-again","move",1),10,11);
            Check(!release.ReleaseConfirmed,"a new press must invalidate the earlier release witness");
            release.Fail("manual_pause");
            Check(!release.ReleaseConfirmed,"failure clearing input is not a release witness");
            release.ObserveReleased("right");
            Check(release.ReleaseConfirmed,"actual release reads after pause may confirm but failure alone cannot");
            var core=Running();
            var move=Request(core,"move","move",2);
            Check(core.Accept(move,10,11), "move accepted");
            Check(core.Held("right") && core.Down("right"), "first input frame has down edge");
            core.Observe("right");
            Check(!core.EndFrame(), "cannot capture before game update");
            core.BeginFrame(10,11,"");
            Check(core.Held("right") && !core.Down("right"), "held does not repeat down edge");
            core.EndFrame();
            core.BeginFrame(10,11,"");
            Check(!core.Held("right") && core.Up("right"), "bounded input releases before capture");
            Finish(core,false);
            Check(!core.Accept(move,10,11), "same command ID never executes twice");

            core=Running();
            var foreign=Request(core,"foreign","move",1); foreign.Session="old-session";
            Check(!core.Accept(foreign,10,11) && !core.Paused, "wrong session ignored");
            core.Accept(Request(core,"unread","move",1),10,11);
            for(int i=0;i<5 && core.PendingReceipt==null;i++)
            { core.EndFrame(); core.BeginFrame(10,11,""); }
            Check(core.PendingReceipt!=null && !core.PendingReceipt.Ok && !core.PendingReceipt.Consumed,
                "missing game input observation fails closed");
            Check(core.Paused && core.HeldCount==0, "unobserved input releases and pauses");

            core=Running(); core.Accept(Request(core,"expired","move",120),10,11); core.Observe("right");
            core.BeginFrame(12,11,"");
            Check(core.Paused && core.HeldCount==0 && core.PendingReceipt.Error=="lease_expired",
                "lease expiry preempts long command");
            Check(core.PendingReceipt.Consumed,"failure preserves whether game read input");

            core=Running(); core.Accept(Request(core,"manual","move",120),10,11);
            core.BeginFrame(10,11,"stop-1");
            Check(core.Paused && core.HeldCount==0 && core.PendingReceipt.Error=="manual_pause",
                "manual pause preempts long command");
            core.AcknowledgeReceipt();
            var stale=Request(core,"late","resume",1); stale.PauseId="";
            core.Accept(stale,10,11);
            Check(core.Paused && !core.PendingReceipt.Ok,"stale resume cannot undo takeover");
            core.AcknowledgeReceipt();
            core.Accept(Request(core,"fresh","resume",1),10,11); Finish(core,false);

            core=Running();
            var invalid=Request(core,"invalid","move",121);
            core.Accept(invalid,10,11);
            Check(core.Paused && core.PendingReceipt.Error=="invalid_command","frame limit enforced");
            core=Running(); core.Accept(Request(core,"capture","move",1),10,11); core.Observe("right");
            core.Fail("capture_failed");
            Check(core.Paused && core.HeldCount==0 && core.PendingReceipt.Consumed,"capture failure releases input");
            core=Running();
            core.BeginFrame(10,11,"new-session-stop");
            core.Accept(Request(core,"resume-after-stop","resume",1),10,11); Finish(core,false);
            core.Accept(Request(core,"active-after-stop","move",120),10,11);
            core.BeginFrame(10,11,Wire.PauseId(new System.Collections.Generic.Dictionary<string,string> {
                {"session_id","old-session"},{"id","late-stop"}},"test-session"));
            Check(!core.Paused && core.Held("right") && core.PauseId=="new-session-stop",
                "foreign pause must not erase a nonempty pause generation or interrupt active input");
            Check(Wire.PauseId(new System.Collections.Generic.Dictionary<string,string> {
                {"session_id","old-session"},{"id","late-stop"}}, "test-session")==null,
                "late pause from another session is ignored");
            Check(Wire.Json(new object[]{"line\n\"\\",true,1.5})=="[\"line\\n\\\"\\\\\",true,1.5]",
                "wire JSON escapes strings and uses invariant numeric formatting");
            Check(!DesktopGuard.Matches(null) && !DesktopGuard.Matches("Default") &&
                !DesktopGuard.Matches("GameQaKit_UnboundDesktop"), "unbound or interactive desktops cannot enable input");
            Console.WriteLine("PASS: "+checks+" Unity control assertions");
            return 0;
        }
        catch(Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
