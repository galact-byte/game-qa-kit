using System;
using System.Collections.Generic;
using GameQaKit.Unity;

internal static class RewiredStateTests
{
    private static int checks;
    private static void Check(bool value,string message)
    { checks++; if(!value) throw new Exception(message); }
    public static int Main()
    {
        try
        {
            var core=new ControlCore("test");
            var bindings=new List<ActionBinding> { new ActionBinding("left",-1),new ActionBinding("right",1) };
            Check(ActionState.Axis(core,bindings)==0,"paused axes are neutral");
            core.BeginFrame(1,10,"");
            core.Accept(new Command { Session="test",Protocol=1,Id="resume",PauseId="",Action="resume",Frames=1 },1,10);
            for(int i=0;i<4;i++) { if(core.EndFrame()) { core.Captured("resume.png"); break; } core.BeginFrame(1,10,""); }
            core.AcknowledgeReceipt();
            core.Accept(new Command { Session="test",Protocol=1,Id="move",PauseId="",Action="move",Direction="left",Frames=1,AfterFrame=core.Frame },1,10);
            Check(ActionState.Axis(core,bindings)==-1,"negative mapping drives a negative axis");
            Check(ActionState.Button(core,bindings,-1,1),"negative down edge is supported");
            Check(!ActionState.Button(core,bindings,1,0),"negative input is not a positive button");
            Check(ActionState.Button(core,bindings,0,0),"any button includes a held mapped input");
            core.EndFrame(); core.BeginFrame(1,10,"");
            Check(!core.ReleaseConfirmed,"clearing bounded input is not a target release read");
            Check(!ActionState.Button(core,bindings,-1,1) && !core.ReleaseConfirmed,"Down=false does not prove release");
            Check(!ActionState.Button(core,bindings,1,0) && !core.ReleaseConfirmed,"another mapping cannot confirm release");
            Check(ActionState.Button(core,bindings,-1,2),"release queries observe the bounded release");
            Check(core.ReleaseConfirmed,"mapped Up query confirms the actual released input");
            Check(!ActionState.Button(core,bindings,-1,1),"release is not a new press");
            Check(ActionState.Axis(core,new ActionBinding[0])==0,"unmapped actions remain neutral");
            core.Fail("pause");
            Check(!ActionState.Button(core,bindings,0,2),"paused release edges cannot trigger game actions");
            Console.WriteLine("PASS: "+checks+" mapped action assertions");
            return 0;
        }
        catch(Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
