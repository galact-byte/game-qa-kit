using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using GameQaKit.Unity;

namespace UnityEngine { internal sealed class WaitForEndOfFrame {} }
namespace UnityEngine.SceneManagement
{
    internal struct Scene { public bool isLoaded; }
    internal static class SceneManager
    {
        internal static Scene GetActiveScene() { return new Scene { isLoaded=true }; }
    }
}

internal sealed partial class BridgeObservationHarness : IDisposable
{
    private IDisposable Trace(string phase) { return null; }
    private readonly ControlCore core=new ControlCore("observation-test");
    private readonly CapturePublisher publisher;
    private CapturePublisher.Publication publication;
    private PhaseDiagnostics diagnostics=null;
    private Dictionary<string,object> captureView,receiptView;
    private string receiptViewId;
    private bool Ready=true;
    private long observationSequence,observationFrame;
    private bool captureImageUsable;
    private bool blankCapture;
    private readonly LoadingWindow loading=new LoadingWindow();
    private readonly string session="observation-test";
    private Dictionary<string,string> Read(string name) { return new Dictionary<string,string>(); }
    private void WriteLoading() {}
    private string observationError;
    private double lastObservation;
    private static double Now;
    private static double CaptureClock { get { return Now; } }
    private int captures,reports,writeThread,writes;
    private readonly int mainThread=Thread.CurrentThread.ManagedThreadId;
    private bool failCapture,failWrite;
    private readonly string directory;
    private readonly ManualResetEvent entered=new ManualResetEvent(false),release=new ManualResetEvent(false),left=new ManualResetEvent(false);
    private readonly List<Receipt> receipts=new List<Receipt>();
    private readonly IEnumerator render;
    private BridgeObservationHarness(string root)
    {
        Now=0;
        directory=Path.Combine(root,Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(FileAt("screenshots"));
        publisher=new CapturePublisher(delegate(string target,byte[] body)
        {
            writeThread=Thread.CurrentThread.ManagedThreadId;
            entered.Set();
            try
            {
                if(!release.WaitOne(3000)) throw new TimeoutException("test writer was not released");
                if(failWrite) throw new IOException("disk unavailable");
                Wire.Write(target,body); Interlocked.Increment(ref writes);
            }
            finally { left.Set(); }
        });
        render=AfterRender();
        Assert(render.MoveNext(),"render coroutine must wait for frame end");
    }
    private string FileAt(string name) { return Path.Combine(directory,name); }
    private byte[] Capture()
    {
        if(failCapture) throw new IOException("capture unavailable");
        captures++;
        captureImageUsable=!blankCapture;
        return new byte[]{1,2,3};
    }
    private Dictionary<string,object> View() { return new Dictionary<string,object>{{"frame",core.Frame}}; }
    private void FlushReceipt()
    {
        var receipt=core.PendingReceipt;
        if(receipt==null) return;
        if(receipt.Ok)
        {
            Assert(receiptViewId==receipt.Request.Id,"receipt must use its own captured state");
            Assert((long)receiptView["frame"]==receipt.CaptureFrame,"state and image must belong to the capture frame");
        }
        receipts.Add(receipt); core.AcknowledgeReceipt(); receiptView=null; receiptViewId=null;
    }
    private void WriteStatus(bool force) {}
    private void TryReport() { reports++; FlushReceipt(); }
    private void Step()
    {
        core.BeginFrame(Now,100,core.PauseId);
        if(core.Held("right")) core.Observe("right");
        render.MoveNext();
    }
    private void Drain()
    {
        var timer=Stopwatch.StartNew();
        while(publication!=null && timer.ElapsedMilliseconds<2500) { Step(); Thread.Sleep(1); }
        Assert(publication==null,"publication did not complete");
    }
    private void Command(string id,string action,int frames)
    {
        Assert(core.Accept(new Command { Protocol=1,Session=core.Session,Id=id,PauseId=core.PauseId,
            AfterFrame=core.Frame,Action=action,Frames=frames,Direction="right" },Now,100),"command must be accepted");
    }
    public void Dispose()
    {
        publisher.Dispose(); release.Set();
        // The delegate must be finished before disposing its test events.
        if(entered.WaitOne(0)) left.WaitOne(3000);
        // Pool completion can still be returning, so the worker-owned events are
        // left to finalization; the process exits immediately after these cases.
    }
    private static void Assert(bool value,string message) { if(!value) throw new Exception(message); }
    private static void Case(string name,Action body)
    { body(); Console.WriteLine("PASS: "+name); }
    private static int Main(string[] args)
    {
        Case("loading cannot recover from a post-load blank image",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.core.BeginFrame(Now,100,"");
                bridge.Command("resume","resume",1);
                bridge.core.BeginFrame(Now,100,"");
                bridge.core.Captured("x"); bridge.core.AcknowledgeReceipt();
                bridge.Command("click","click",1);
                bridge.core.Fail("pause"); bridge.FlushReceipt();
                bridge.core.ObserveReleased("mouse0");
                bridge.loading.Begin("Next",bridge.core,Now,0);
                bridge.loading.Loaded("Next",bridge.core.Frame);
                bridge.blankCapture=true; bridge.release.Set(); bridge.Step(); bridge.Drain();
                Assert(bridge.loading.Active && bridge.core.Paused,"a published black image must not release the loading gate");
                bridge.blankCapture=false; Now=.6; bridge.Step(); bridge.Drain();
                Assert(!bridge.loading.Active && bridge.core.Paused,"a later valid image may recover without resuming input");
            }
        });
        Case("paused slow publication stays off the game thread and cannot accumulate",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Ready=false; bridge.Step();
                Assert(bridge.captures==0,"unready backend must not capture");
                bridge.Ready=true;
                var elapsed=Stopwatch.StartNew(); bridge.Step();
                Assert(elapsed.ElapsedMilliseconds<200,"blocked file writer must not block rendering");
                Assert(bridge.entered.WaitOne(2000),"writer did not start");
                Assert(bridge.writeThread!=bridge.mainThread,"PNG file publication must leave the game thread");
                for(int i=0;i<20;i++) bridge.Step();
                Assert(bridge.captures==1 && bridge.observationSequence==0,"pending write must neither accumulate nor confirm an image");
                Assert(!bridge.publisher.Start(new CapturePublisher.Publication(null,1,0),new byte[]{1},null,bridge.FileAt("other.png")),"single slot must reject a second job");
                Assert(!File.Exists(bridge.FileAt("screenshots/startup.png")),"old image must not be acknowledged");
                bridge.release.Set(); bridge.Drain();
                Assert(bridge.observationSequence==1 && File.Exists(bridge.FileAt("screenshots/startup.png")),"successful publish must acknowledge its image");
                Assert(bridge.core.Paused && bridge.core.Active==null,"passive capture must not resume input");
                bridge.Step(); Assert(bridge.captures==1,"passive capture must remain rate limited");
                Now=.5; bridge.Step(); bridge.Drain();
                Assert(bridge.observationSequence==2,"paused image must refresh");
            }
        });
        Case("completed publication remains valid when the game thread polls later",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Step(); Assert(bridge.entered.WaitOne(2000),"writer did not start");
                bridge.release.Set(); Assert(bridge.left.WaitOne(2000),"writer did not finish");
                Thread.Sleep(20);
                Now=3; bridge.Step();
                Assert(bridge.observationSequence==1 && bridge.observationError==null,
                    "a PNG completed within its deadline must not expire while awaiting the game-thread poll");
            }
        });
        Case("completed publication beyond its deadline is still rejected",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Step(); Assert(bridge.entered.WaitOne(2000),"writer did not start");
                Thread.Sleep(2100);
                bridge.release.Set(); Assert(bridge.left.WaitOne(2000),"writer did not finish");
                Thread.Sleep(20);
                Now=.5; bridge.Step();
                Assert(bridge.observationSequence==0 && bridge.observationError!=null,
                    "a completed late PNG must not pass its publication deadline");
            }
        });
        Case("action waits for both files and retains the original capture frame",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Command("resume","resume",1);
                for(int i=0;i<3;i++) bridge.Step();
                Assert(bridge.publication!=null && bridge.receipts.Count==0,"action must await file publication");
                long captured=bridge.publication.Frame;
                for(int i=0;i<8;i++) bridge.Step();
                Assert(bridge.core.HeldCount==0 && bridge.receipts.Count==0,"pending publication must not retain input or report success");
                bridge.release.Set(); bridge.Drain();
                Assert(bridge.receipts.Count==1 && bridge.receipts[0].Ok,"action must complete after publishing");
                Assert(bridge.receipts[0].CaptureFrame==captured && captured<bridge.core.Frame,"receipt must not relabel the image with a later frame");
                Assert(bridge.writes==2,"action image and preview must both publish");
                bridge.Command("move","move",3);
                bridge.Step(); bridge.Step();
                Assert(bridge.publication==null && bridge.captures==1,"passive capture must not stretch an active input's frame budget");
                bridge.Step(); bridge.Step(); bridge.Step(); bridge.Drain();
                Assert(bridge.receipts.Count==2 && bridge.receipts[1].Consumed,"movement must retain its input consumption proof");
            }
        });
        Case("pause invalidates a late action publication",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Command("resume","resume",1);
                for(int i=0;i<3;i++) bridge.Step();
                Assert(bridge.entered.WaitOne(2000),"writer did not start");
                bridge.core.Fail("manual_pause"); bridge.FlushReceipt();
                bridge.release.Set(); bridge.Drain();
                Assert(bridge.receipts.Count==1 && !bridge.receipts[0].Ok && bridge.core.Paused,"late PNG must not turn a failed action into success");
                Assert(bridge.observationSequence==0,"cancelled action image must not advance observation sequence");
            }
        });
        Case("write and encode failures invalidate observation and stop further captures",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.failWrite=true; bridge.release.Set(); bridge.Step(); bridge.Drain();
                Assert(bridge.reports==1 && bridge.observationSequence==0 && bridge.core.Paused,"write failure must fail closed");
                Assert(bridge.observationError!=null,"write failure must invalidate input readiness");
                Now=1; bridge.Step(); Assert(bridge.captures==1,"write error must not retry indefinitely");
            }
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.failCapture=true; bridge.Step();
                Assert(bridge.reports==1 && bridge.observationSequence==0,"encoding failure must be reported");
            }
        });
        Case("publication deadline stops input without waiting for filesystem completion",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Step(); Assert(bridge.entered.WaitOne(2000),"writer did not start");
                Now=2; bridge.Step(); Assert(bridge.reports==0,"existing two-second boundary must be inclusive");
                Now=2.001; var elapsed=Stopwatch.StartNew(); bridge.Step();
                Assert(elapsed.ElapsedMilliseconds<200 && bridge.reports==1,"timeout must not join a blocked writer");
                bridge.release.Set(); Assert(bridge.left.WaitOne(2000),"writer must exit after the test releases it");
                bridge.Step(); Assert(bridge.observationSequence==0 && bridge.core.Paused,"late completion must not restore control");
            }
        });
        Case("dispose prevents completion and the next target after a blocked write",delegate
        {
            using(var bridge=new BridgeObservationHarness(args[0]))
            {
                bridge.Command("resume","resume",1);
                for(int i=0;i<3;i++) bridge.Step();
                Assert(bridge.entered.WaitOne(2000),"writer did not start");
                bridge.publisher.Dispose(); bridge.release.Set();
                Assert(bridge.left.WaitOne(2000),"writer must finish");
                Assert(bridge.publisher.Take()==null,"disposed publisher cannot return success");
                Assert(!File.Exists(bridge.FileAt("screenshots/startup.png")),"disposed publisher cannot begin the preview write");
            }
        });
        Console.WriteLine("Unity paused observation assertions: passed");
        return 0;
    }
}
