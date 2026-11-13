using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using GameQaKit.Unity;

internal static class PhaseDiagnosticsTests
{
    private static void Check(bool value,string message) { if(!value) throw new Exception(message); }
    private static string Read(string path)
    {
        using(var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))
        using(var reader=new StreamReader(file)) return reader.ReadToEnd();
    }
    private static string Await(string path,string marker)
    {
        var watch=Stopwatch.StartNew();
        while(watch.ElapsedMilliseconds<2000)
        {
            if(File.Exists(path))
            {
                try { string text=Read(path); if(text.Contains(marker)) return text; }
                catch(IOException) {}
            }
            Thread.Sleep(10);
        }
        throw new Exception("snapshot missing "+marker);
    }
    private static void AwaitIdle(PhaseDiagnostics trace)
    {
        var writing=typeof(PhaseDiagnostics).GetField("writing",BindingFlags.Instance|BindingFlags.NonPublic);
        var watch=Stopwatch.StartNew();
        while((int)writing.GetValue(trace)!=0 && watch.ElapsedMilliseconds<2000) Thread.Sleep(1);
        Check((int)writing.GetValue(trace)==0,"diagnostic writer did not finish");
    }
    private static IDisposable Enter(PhaseDiagnostics trace,int frame)
    { return trace==null ? null : trace.Enter("tick",frame,0); }
    public static int Main(string[] args)
    {
        string disabled=Path.Combine(args[0],"disabled");
        Check(PhaseDiagnostics.Open(disabled,"test",false)==null,"disabled diagnostics must have no resource");
        Check(!Directory.Exists(disabled),"disabled diagnostics must not create files");
        string path=Path.Combine(args[0],"diagnostics.json");
        var trace=PhaseDiagnostics.Open(args[0],"test",true);
        Check(trace!=null,"enabled diagnostics must start");
        using(trace.Enter("tick",41,2))
        {
            using(trace.Enter("view",41,2))
            {
                string text=Await(path,"\"phase\":\"view\"");
                Thread.Sleep(2250);
                text=Read(path);
                Check(text.Contains("\"phase\":\"view\"") && text.Contains("\"frame\":41"),
                      "phase and frame must survive a stall longer than the two-second control boundary");
                Check(!text.Contains("updated_at") && !text.Contains("input_ready"),"diagnostic must not publish control freshness");
            }
            Await(path,"\"phase\":\"tick\"");
        }
        string gap=Await(path,"\"phase\":\"between_callbacks\"");
        Check(gap.Contains("\"view\""),"completed view time must be retained");
        Thread.Sleep(300);
        using(trace.Enter("tick",42,3)) {}
        gap=Await(path,"\"frame\":42");
        Check(gap.Contains("\"between_callbacks\":{\"last_ms\":"),"gap maximum retained after recovery");
        Check(gap.Contains("\"sampled_at\":"),"wall clock correlation available");
        using(trace.Enter("capture",42,3))
        using(trace.Enter("png_encode",42,3)) Await(path,"\"phase\":\"png_encode\"");
        // Execute the real final publication without hundreds of unrelated disk writes.
        var publish=typeof(PhaseDiagnostics).GetMethod("Publish",BindingFlags.Instance|BindingFlags.NonPublic);
        var timer=(Timer)typeof(PhaseDiagnostics).GetField("timer",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(trace);
        timer.Change(Timeout.Infinite,Timeout.Infinite);
        AwaitIdle(trace);
        typeof(PhaseDiagnostics).GetField("writes",BindingFlags.Instance|BindingFlags.NonPublic).SetValue(trace,479);
        publish.Invoke(trace,new object[]{null});
        for(int i=0;i<600;i++) publish.Invoke(trace,new object[]{null});
        AwaitIdle(trace);
        Check(new FileInfo(path).Length<8192,"diagnostic snapshot must be bounded");
        string capped=Read(path);
        publish.Invoke(trace,new object[]{null});
        Check(Read(path)==capped,"publication budget must stop further writes");
        trace.Dispose();
        // Dispose does not block the game thread on a write already in flight.
        AwaitIdle(trace);
        string stopped=Read(path);
        Thread.Sleep(300);
        Check(Read(path)==stopped,"disposed timer must stop publishing");
        string blocked=Path.Combine(args[0],"blocked");
        File.WriteAllText(blocked,"occupied");
        var failing=PhaseDiagnostics.Open(blocked,"test",true);
        if(failing!=null)
        {
            using(failing.Enter("view",1,1)) Thread.Sleep(300);
            failing.Dispose();
        }
        var benchmark=Stopwatch.StartNew();
        for(int i=0;i<100000;i++) using(Enter(null,i)) {}
        double off=benchmark.Elapsed.TotalMilliseconds;
        trace=PhaseDiagnostics.Open(args[0],"benchmark",true);
        benchmark.Restart();
        for(int i=0;i<100000;i++) using(Enter(trace,i)) {}
        double on=benchmark.Elapsed.TotalMilliseconds;
        trace.Dispose();
        Console.WriteLine("diagnostics benchmark: 100000 scopes disabled_ms="+off+" enabled_ms="+on);
        Console.WriteLine("Unity diagnostics assertions: passed");
        return 0;
    }
}
