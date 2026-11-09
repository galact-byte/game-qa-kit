using System;
using System.Collections.Generic;

namespace GameQaKit.Unity
{
    public sealed class ActionBinding
    {
        public readonly string Token;
        public readonly int Sign;
        public ActionBinding(string token,int sign)
        { Token=token; Sign=sign<0 ? -1 : 1; }
    }
    public static class ActionState
    {
        public static float Axis(ControlCore core,IEnumerable<ActionBinding> bindings)
        {
            if(core.Paused) return 0;
            float value=0;
            foreach(var binding in bindings)
                if(core.Held(binding.Token)) { core.Observe(binding.Token); value+=binding.Sign; }
            return Math.Max(-1,Math.Min(1,value));
        }
        public static bool Button(ControlCore core,IEnumerable<ActionBinding> bindings,int polarity,int edge)
        {
            if(core.Paused) return false;
            bool value=false;
            foreach(var binding in bindings)
            {
                if(polarity!=0 && binding.Sign!=polarity) continue;
                bool active=edge==1 ? core.Down(binding.Token) : edge==2 ? core.Up(binding.Token) : core.Held(binding.Token);
                if(active) { core.Observe(binding.Token); value=true; }
            }
            return value;
        }
    }
}
