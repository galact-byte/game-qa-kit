using System;
using System.IO;
using System.Linq;
using System.Reflection;
using Mono.Cecil;
using Mono.Cecil.Cil;
using GameQaKit.Bakin;
public class PatchTests
{
    public static int Count;
    public static void Hook() { Count++; }
    static void Reject(Action action) { try { action(); } catch(InvalidOperationException) { return; } throw new Exception("Unsupported structure accepted"); }
    static MethodDefinition Run(ModuleDefinition module,TypeReference returns)
    {
        var type=new TypeDefinition("Fixture","Target",Mono.Cecil.TypeAttributes.Public|Mono.Cecil.TypeAttributes.Class,module.TypeSystem.Object);
        module.Types.Add(type);
        var method=new MethodDefinition("Run",Mono.Cecil.MethodAttributes.Public|Mono.Cecil.MethodAttributes.Static,returns);
        type.Methods.Add(method);return method;
    }
    static MethodReference LauncherHook(ModuleDefinition m)
    {
        var hook=new MethodReference("Record",m.ImportReference(typeof(System.Diagnostics.Process)),m.TypeSystem.Object);
        hook.Parameters.Add(new ParameterDefinition(m.TypeSystem.String));hook.Parameters.Add(new ParameterDefinition(m.TypeSystem.String));return hook;
    }
    static int Main(string[] args)
    {
        try
        {
            using(var m=ModuleDefinition.CreateModule("PatchFixture_"+Guid.NewGuid().ToString("N"),ModuleKind.Dll))
            {
                if(args[0]=="branch_runtime")
                {
                    var method=Run(m,m.TypeSystem.Int32);var il=method.Body.GetILProcessor();var ret=il.Create(OpCodes.Ret);
                    il.Append(il.Create(OpCodes.Ldc_I4,7));il.Append(il.Create(OpCodes.Br_S,ret));il.Append(ret);
                    var hook=m.ImportReference(typeof(PatchTests).GetMethod("Hook"));
                    foreach(string name in new[]{"Before","After"}) typeof(PatchTool).GetMethod(name,BindingFlags.NonPublic|BindingFlags.Static).Invoke(null,new object[]{method,hook});
                    using(var memory=new MemoryStream()) { m.Write(memory);var asm=Assembly.Load(memory.ToArray());var value=asm.GetType("Fixture.Target").GetMethod("Run").Invoke(null,null);if((int)value!=7||Count!=2)throw new Exception("Rewritten control flow failed"); }
                    if(method.Body.Instructions.Any(i=>i.OpCode.OperandType==OperandType.ShortInlineBrTarget))throw new Exception("Unexpanded short branch");
                }
                else if(args[0]=="launch_count" || args[0]=="launch_return")
                {
                    var method=Run(m,m.TypeSystem.Void);var il=method.Body.GetILProcessor();
                    var start=m.ImportReference(typeof(System.Diagnostics.Process).GetMethod("Start",new[]{typeof(string),typeof(string)}));
                    for(int i=0;i<(args[0]=="launch_count"?2:1);i++) { il.Append(il.Create(OpCodes.Call,start)); if(args[0]=="launch_count")il.Append(il.Create(OpCodes.Pop)); }
                    il.Append(il.Create(OpCodes.Ret));Reject(()=>PatchTool.ReplaceLaunch(method,LauncherHook(m)));
                }
                else if(args[0]=="launch_valid")
                {
                    var method=Run(m,m.TypeSystem.Void);var il=method.Body.GetILProcessor();var start=m.ImportReference(typeof(System.Diagnostics.Process).GetMethod("Start",new[]{typeof(string),typeof(string)}));
                    var instruction=il.Create(OpCodes.Call,start);il.Append(instruction);il.Append(il.Create(OpCodes.Pop));il.Append(il.Create(OpCodes.Ret));var hook=LauncherHook(m);
                    PatchTool.ReplaceLaunch(method,hook);if(instruction.Operand!=hook)throw new Exception("Final call not replaced");
                }
                else if(args[0]=="query_signature")
                {
                    var method=Run(m,m.TypeSystem.Single);method.Parameters.Add(new ParameterDefinition(m.TypeSystem.Boolean));method.Body.Instructions.Add(Instruction.Create(OpCodes.Ret));
                    var hook=new MethodReference("Wrong",m.TypeSystem.Single,m.TypeSystem.Object);hook.Parameters.Add(new ParameterDefinition(m.TypeSystem.Int32));
                    try { typeof(PatchTool).GetMethod("ReplaceQuery",BindingFlags.NonPublic|BindingFlags.Static).Invoke(null,new object[]{method,hook}); }
                    catch(TargetInvocationException e) { if(e.InnerException is InvalidOperationException) { Console.WriteLine("PASS "+args[0]);return 0; } throw; }
                    throw new Exception("Wrong query parameter type accepted");
                }
                else throw new Exception("Unknown case");
            }
            Console.WriteLine("PASS "+args[0]);return 0;
        }
        catch(Exception e) {Console.Error.WriteLine(e);return 1;}
    }
}
