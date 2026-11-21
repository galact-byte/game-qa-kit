using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace GameQaKit.Bakin
{
    public static class PatchTool
    {
        const string LauncherDigest = "e1386451c4ce03fb529a62676b76f366b4677ee3a95b7993fd0ab4cd99d3b216";
        const string EngineDigest = "e79836201d17e5c40754011df9f96e99c92be61b95d674195581bc36e80e5a19";
        public static string Digest(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }
        static MethodDefinition Method(ModuleDefinition module, string type, string signature)
        {
            var candidate = module.Types.Single(t => t.FullName == type).Methods.Single(m => m.FullName == signature);
            if (!candidate.HasBody) throw new InvalidOperationException("Missing registered method body");
            return candidate;
        }
        public static void ReplaceLaunch(MethodDefinition method, MethodReference hook)
        {
            var calls = method.Body.Instructions.Where(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference
                && ((MethodReference)i.Operand).FullName == "System.Diagnostics.Process System.Diagnostics.Process::Start(System.String,System.String)").ToArray();
            if (calls.Length != 1 || calls[0].Next == null || calls[0].Next.OpCode != OpCodes.Pop
                || hook.ReturnType.FullName != "System.Diagnostics.Process"
                || hook.Parameters.Count != 2 || hook.Parameters.Any(p => p.ParameterType.FullName != "System.String"))
                throw new InvalidOperationException("Unexpected launch call, used return value, or hook signature");
            calls[0].Operand = hook;
        }
        static void ExpandBranches(MethodDefinition method)
        {
            foreach (var instruction in method.Body.Instructions)
            {
                if (instruction.OpCode.OperandType != OperandType.ShortInlineBrTarget) continue;
                string name = instruction.OpCode.Name.Substring(0, instruction.OpCode.Name.Length - 2);
                var field = typeof(OpCodes).GetFields().Single(f => f.FieldType == typeof(OpCode)
                    && ((OpCode)f.GetValue(null)).Name == name);
                instruction.OpCode = (OpCode)field.GetValue(null);
            }
        }
        static void Before(MethodDefinition method, MethodReference hook)
        {
            ExpandBranches(method);
            method.Body.GetILProcessor().InsertBefore(method.Body.Instructions[0], Instruction.Create(OpCodes.Call, hook));
        }
        static void After(MethodDefinition method, MethodReference hook)
        {
            ExpandBranches(method);
            var il = method.Body.GetILProcessor();
            foreach (var instruction in method.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
            {
                instruction.OpCode = OpCodes.Call;
                instruction.Operand = hook;
                il.InsertAfter(instruction, Instruction.Create(OpCodes.Ret));
            }
        }
        static void ReplaceQuery(MethodDefinition method, MethodReference hook)
        {
            if (method.HasThis || hook.HasThis || method.Parameters.Count != hook.Parameters.Count
                || method.ReturnType.FullName != hook.ReturnType.FullName
                || !method.Parameters.Select(p => p.ParameterType.FullName).SequenceEqual(hook.Parameters.Select(p => p.ParameterType.FullName)))
                throw new InvalidOperationException("Unexpected query hook signature");
            method.Body.Instructions.Clear();
            method.Body.ExceptionHandlers.Clear();
            method.Body.Variables.Clear();
            method.Body.InitLocals = false;
            var il = method.Body.GetILProcessor();
            foreach (var parameter in method.Parameters) il.Append(il.Create(OpCodes.Ldarg, parameter));
            il.Append(il.Create(OpCodes.Call, hook));
            il.Append(il.Create(OpCodes.Ret));
            method.Body.MaxStackSize = Math.Max(8, method.Parameters.Count);
        }
        static void PatchEngine(ModuleDefinition module, ModuleDefinition bridge, string profile)
        {
            var type = bridge.Types.Single(t => t.FullName == "GameQaKit.Bakin.RuntimeBridge");
            Func<string, MethodReference> hook = name => module.ImportReference(type.Methods.Single(m => m.Name == name));
            var config = Method(module, "Yukar.Engine.Config", "System.Boolean Yukar.Engine.Config::Initialize(System.String,System.String)");
            ExpandBranches(config);
            var il = config.Body.GetILProcessor();
            var head = config.Body.Instructions[0];
            il.InsertBefore(head, il.Create(OpCodes.Ldstr, Path.GetFullPath(profile)));
            il.InsertBefore(head, il.Create(OpCodes.Starg, config.Parameters[0]));
            var input = Method(module, "Yukar.Engine.Input", "System.Void Yukar.Engine.Input::Update()");
            Before(input, hook("BeforeInput")); After(input, hook("AfterInput"));
            ReplaceQuery(Method(module, "Yukar.Engine.Input", "Yukar.Engine.Input/KeyStates Yukar.Engine.Input::GetState(Yukar.Engine.Input/StateType,Yukar.Engine.Input/GameState)"), hook("GetState"));
            ReplaceQuery(Method(module, "Yukar.Engine.Input", "System.Single Yukar.Engine.Input::GetAxis(Yukar.Engine.Input/KeyStates,Yukar.Engine.Input/GameState,System.Boolean,System.Boolean)"), hook("GetAxis"));
            ReplaceQuery(Method(module, "Yukar.Engine.Input", "System.Void Yukar.Engine.Input::SetTriggerConsumed(Yukar.Engine.Input/KeyStates,Yukar.Engine.Input/GameState)"), hook("ConsumeTrigger"));
            After(Method(module, "Yukar.Engine.GameMain", "System.Void Yukar.Engine.GameMain::draw()"), hook("AfterDraw"));
        }
        static int Main(string[] args)
        {
            try
            {
                if (args.Length != 6 || (args[0] != "launcher" && args[0] != "engine"))
                    throw new ArgumentException("Expected mode, source, destination, bridge, profile, report");
                string expected = args[0] == "launcher" ? LauncherDigest : EngineDigest;
                if (Digest(args[1]) != expected) throw new InvalidOperationException("Unregistered original component digest");
                var resolver = new DefaultAssemblyResolver();
                resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(args[1])));
                resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(args[3])));
                using (var assembly = AssemblyDefinition.ReadAssembly(args[1], new ReaderParameters { AssemblyResolver = resolver, InMemory = true }))
                using (var bridge = AssemblyDefinition.ReadAssembly(args[3], new ReaderParameters { AssemblyResolver = resolver }))
                {
                    var module = assembly.MainModule;
                    if (module.AssemblyReferences.Any(r => r.Name.StartsWith("GameQaKit.Bakin", StringComparison.Ordinal)))
                        throw new InvalidOperationException("Component is already patched");
                    if (args[0] == "launcher")
                    {
                        var method = Method(module, "Yukar_Engine_Launcher.Form1", "System.Void Yukar_Engine_Launcher.Form1::doExecuteEngine()");
                        var hook = bridge.MainModule.Types.Single(t => t.FullName == "GameQaKit.Bakin.PreparedLaunch").Methods.Single(m => m.Name == "Record");
                        ReplaceLaunch(method, module.ImportReference(hook));
                    }
                    else PatchEngine(module, bridge.MainModule, args[4]);
                    string temporary = args[2] + ".patched";
                    if (File.Exists(temporary)) throw new IOException("Patch temporary already exists");
                    assembly.Write(temporary);
                    using (var checkedAssembly = AssemblyDefinition.ReadAssembly(temporary))
                        if (!checkedAssembly.MainModule.AssemblyReferences.Any(r => r.Name.StartsWith("GameQaKit.Bakin", StringComparison.Ordinal)))
                            throw new InvalidOperationException("Patch reference was not emitted");
                    string outputDigest = Digest(temporary);
                    if (File.Exists(args[2])) File.Replace(temporary, args[2], null); else File.Move(temporary, args[2]);
                    File.WriteAllText(args[5], "mode=" + args[0] + Environment.NewLine + "original=" + expected + Environment.NewLine + "patched=" + outputDigest + Environment.NewLine);
                    Console.WriteLine(outputDigest);
                }
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error.ToString()); return 1; }
        }
    }
}
