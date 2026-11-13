using System;
using System.Collections;
using System.IO;
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

internal sealed partial class BridgeObservationHarness
{
    private IDisposable Trace(string phase) { return null; }
    private ControlCore core = new ControlCore("observation-test");
    private bool Ready { get; set; }
    private long observationSequence { get; set; }
    private string observationError { get; set; }
    private double lastObservation { get; set; }
    private static double Now { get; set; }
    private int captures, reports;
    private bool failCapture;
    private string directory;
    private string FileAt(string name) { return Path.Combine(directory,name); }
    private void Capture(string target)
    {
        if(failCapture) throw new IOException("capture unavailable");
        captures++;
        Directory.CreateDirectory(Path.GetDirectoryName(target));
        File.WriteAllBytes(target,new byte[]{1,2,3});
    }
    private void FlushReceipt() {}
    private void WriteStatus(bool force) {}
    private void TryReport() { reports++; }
    private static void Assert(bool value,string message)
    {
        if(!value) throw new Exception(message);
    }
    private static int Main(string[] args)
    {
        var bridge = new BridgeObservationHarness { directory=args[0] };
        var render = bridge.AfterRender();
        Assert(render.MoveNext(),"render coroutine must wait for frame end");
        Assert(bridge.captures==0,"must not capture before end of frame");
        render.MoveNext();
        Assert(bridge.captures==0,"unready backend must not publish a frame");
        bridge.Ready=true;
        render.MoveNext();
        Assert(bridge.captures==1,"initial paused frame must be captured without a command");
        Assert(bridge.observationSequence==1,"capture must publish an observation sequence");
        Assert(bridge.core.Paused && bridge.core.Active==null,"observation must not resume input or invent a command");
        Assert(File.Exists(bridge.FileAt("screenshots/startup.png")),"GUI startup preview must exist");
        render.MoveNext();
        Assert(bridge.captures==1,"passive capture must be rate limited");
        Now=.5;
        render.MoveNext();
        Assert(bridge.captures==2 && bridge.observationSequence==2,"paused observation must refresh at its interval");
        bridge.failCapture=true;
        Now=1;
        render.MoveNext();
        Assert(bridge.reports==1,"capture failure must be reported");
        Assert(bridge.observationSequence==0,"failed observation must invalidate the previous preview");
        Assert(bridge.core.Paused && bridge.core.Active==null,"capture failure must keep input paused");
        Now=2;
        render.MoveNext();
        Assert(bridge.reports==1 && bridge.captures==2,"failed passive observation must stop until explicit recovery");
        Console.WriteLine("Unity paused observation assertions: passed");
        return 0;
    }
}
