using System;
using GameQaKit.Unity;

namespace GameQaKit.Unity
{
    internal static partial class LegacyInput
    {
        internal static ControlCore Core;
        internal static bool Query(string token,int edge) { return Read(token,edge); }
    }
}

internal static class LegacyReleaseTests
{
    private static void Check(bool value,string message)
    { if(!value) throw new Exception(message); }
    public static int Main()
    {
        try
        {
            foreach(bool missedEdge in new[]{false,true})
            {
            var core=new ControlCore("test"); LegacyInput.Core=core;
            core.BeginFrame(1,10,"");
            core.Accept(new Command { Session="test",Protocol=1,Id="resume",PauseId="",Action="resume",Frames=1 },1,10);
            core.Captured("resume.png"); core.AcknowledgeReceipt();
            core.Accept(new Command { Session="test",Protocol=1,Id="click",PauseId="",Action="click",Frames=1,AfterFrame=core.Frame },1,10);
            Check(LegacyInput.Query("mouse0",0) && !core.ReleaseConfirmed,"held mouse query is not release");
            core.EndFrame(); core.BeginFrame(1,10,"");
            Check(!LegacyInput.Query("mouse0",1) && !core.ReleaseConfirmed,"Down=false must not acknowledge mouse release");
            Check(!LegacyInput.Query("ok",0) && !core.ReleaseConfirmed,"unrelated button must not acknowledge mouse release");
            if(missedEdge)
            {
                core.BeginFrame(1,10,"");
                Check(!LegacyInput.Query("mouse0",2) && !core.ReleaseConfirmed,"Up=false after a missed edge must not acknowledge release");
                Check(!LegacyInput.Query("mouse0",0) && core.ReleaseConfirmed,"Held=false proves the actual neutral mouse state");
            }
            else Check(LegacyInput.Query("mouse0",2) && core.ReleaseConfirmed,"actual mouse Up query acknowledges bounded release");
            long frame=core.ReleaseFrame;
            core.BeginFrame(1,10,"");
            Check(!LegacyInput.Query("mouse0",0) && core.ReleaseFrame==frame,"repeated neutral queries do not refresh release proof");
            }
            Console.WriteLine("PASS: Unity target release queries");
            return 0;
        }
        catch(Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
