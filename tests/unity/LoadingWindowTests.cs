using System;
using GameQaKit.Unity;

internal static class LoadingWindowTests
{
    private static void Check(bool value,string message) { if(!value) throw new Exception(message); }
    private static ControlCore Released()
    {
        var core=new ControlCore("loading-test");
        core.BeginFrame(100,200,"");
        core.Accept(new Command{Protocol=1,Session=core.Session,Id="resume",PauseId="",Action="resume",Frames=1},100,200);
        core.Fail("pause"); core.AcknowledgeReceipt();
        core.Accept(new Command{Protocol=1,Session=core.Session,Id="resume2",PauseId="",Action="resume",Frames=1},100,200);
        core.Captured("x"); core.AcknowledgeReceipt();
        core.Accept(new Command{Protocol=1,Session=core.Session,Id="click",PauseId="",Action="click",Frames=1},100,200);
        core.Fail("pause"); core.AcknowledgeReceipt();
        core.ObserveReleased("mouse0");
        return core;
    }
    private static int Main()
    {
        try
        {
            Check(!LoadingWindow.ImageUsable(1024,768,delegate(int index) { return 0; }),"pure black cannot recover loading");
            Check(!LoadingWindow.ImageUsable(1024,768,delegate(int index) { return 0xffffff; }),"pure white cannot recover loading");
            Check(!LoadingWindow.ImageUsable(1024,768,delegate(int index) { return index%1024==0 ? 0x123456 : 0; }),"edge pixels cannot release the blank image gate");
            Check(LoadingWindow.ImageUsable(1024,768,delegate(int index) { return 0x010101; }),"visible dark image uses the existing exact-black criterion");
            var core=Released();
            var window=new LoadingWindow();
            window.Begin("Common",core,100,7);
            string id=window.Id;
            window.Begin("Intro",core,100.001,7);
            Check(core.Paused && core.HeldCount==0 && window.Eligible,"loading requires release evidence and pauses immediately");
            window.Loaded("Common",core.Frame);
            Check(!window.Observe(core.Frame+1,core,101),"one scene cannot recover a two-scene load");
            window.Loaded("Intro",core.Frame);
            Check(!window.Observe(core.Frame,core,102),"a pre-load queued image cannot recover");
            Check(window.Observe(core.Frame+1,core,102),"a fresh rendered frame recovers paused");
            Check(!window.Active && window.Id==id && core.Paused,"recovery does not resume or change generation");
            window.Begin("Next",core,110,8);
            Check(window.Id!=id,"a later load has a distinct generation");
            double started=window.StartedAt;
            window.Begin("Additive",core,111,8);
            Check(window.StartedAt==started,"additional requests do not extend the deadline");
            window.Loaded("Next",core.Frame); window.Loaded("Additive",core.Frame);
            Check(!window.Observe(core.Frame+1,core,120),"a timed out load cannot recover");
            var unproven=new LoadingWindow();
            unproven.Begin("Bad",new ControlCore("unknown"),100,0);
            Check(!unproven.Eligible,"empty held keys are not a release proof");
            var queued=new Command{Protocol=1,Session=core.Session,Id="queued",PauseId="",Action="press",Button="ok",Frames=1};
            Check(core.Reject(queued,"scene_loading"),"queued input is rejected");
            Check(core.ReleaseConfirmed && core.HeldCount==0 && core.Paused,"rejection cannot invalidate real release evidence");
            core.AcknowledgeReceipt();
            Check(!core.Accept(queued,121,200),"rejected commands cannot replay after recovery");
            Console.WriteLine("PASS: Unity bounded loading safety"); return 0;
        }
        catch(Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
