using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using HarmonyLib;
using Mono.Cecil;

namespace GameQaKit.Unity
{
    public sealed class InputRouter : IDisposable
    {
        private readonly Harmony harmony=new Harmony("org.gameqakit.unity.legacy");
        private static readonly Dictionary<string,MethodInfo> replacements=typeof(LegacyInput)
            .GetMethods(BindingFlags.Public|BindingFlags.Static).ToDictionary(
                m=>Signature(m.Name,m.GetParameters().Select(p=>p.ParameterType.FullName)),m=>m);
        private readonly List<KeyValuePair<FieldInfo,Delegate>> redirectedDelegates=new List<KeyValuePair<FieldInfo,Delegate>>();
        public int PatchedMethods { get; private set; }
        public static string Signature(string name,IEnumerable<string> arguments)
        { return name+"("+string.Join(",",arguments.ToArray())+")"; }
        public void Install(string managedDirectory,bool allowRewired=false)
        {
            var targets=new List<MethodBase>();
            var delegateTypes=new HashSet<Type>();
            foreach(Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if(assembly is System.Reflection.Emit.AssemblyBuilder || string.IsNullOrEmpty(assembly.Location)) continue;
                string file=Path.GetFullPath(assembly.Location);
                string name=assembly.GetName().Name;
                if(!file.StartsWith(Path.GetFullPath(managedDirectory)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) continue;
                if((name.StartsWith("Unity") && name!="UnityEngine.UI") || name.StartsWith("System") ||
                    name.StartsWith("Mono.") || name.StartsWith("I18N") || name=="mscorlib" || name=="netstandard") continue;
                if(allowRewired && name.StartsWith("Rewired")) continue;
                using(var definition=AssemblyDefinition.ReadAssembly(file))
                {
                    if(definition.MainModule.AssemblyReferences.Any(r=>r.Name=="Unity.InputSystem" || (!allowRewired && r.Name.StartsWith("Rewired"))))
                        throw new NotSupportedException("New Input System / Rewired requires a separate input backend: "+name);
                    foreach(TypeDefinition type in Types(definition.MainModule.Types))
                    foreach(MethodDefinition method in type.Methods)
                    {
                        if(!method.HasBody) continue;
                        bool reads=false;
                        foreach(var instruction in method.Body.Instructions)
                        {
                            var call=instruction.Operand as MethodReference;
                            if(call==null || call.DeclaringType.FullName!="UnityEngine.Input") continue;
                            if((instruction.OpCode.Code!=Mono.Cecil.Cil.Code.Call && instruction.OpCode.Code!=Mono.Cecil.Cil.Code.Ldftn) ||
                                !replacements.ContainsKey(Signature(call.Name,call.Parameters.Select(p=>p.ParameterType.FullName))))
                                throw new NotSupportedException("Unmapped legacy input call: "+call.FullName);
                            reads=true;
                            if(instruction.OpCode.Code==Mono.Cecil.Cil.Code.Ldftn && method.IsConstructor && method.IsStatic)
                                delegateTypes.Add(assembly.ManifestModule.ResolveType(type.MetadataToken.ToInt32()));
                        }
                        if(!reads) continue;
                        var target=assembly.ManifestModule.ResolveMethod(method.MetadataToken.ToInt32());
                        if(target.ContainsGenericParameters) throw new NotSupportedException("Generic input caller requires an explicit adapter: "+target);
                        targets.Add(target);
                    }
                }
            }
            if(targets.Count==0 && !allowRewired) throw new NotSupportedException("No managed legacy input call sites found");
            foreach(MethodBase method in targets)
            {
                harmony.Patch(method,transpiler:new HarmonyMethod(typeof(InputRouter),"Redirect"));
                PatchedMethods++;
            }
            foreach(Type type in delegateTypes)
            foreach(FieldInfo field in type.GetFields(BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic))
            {
                if(!typeof(Delegate).IsAssignableFrom(field.FieldType)) continue;
                var original=field.GetValue(null) as Delegate;
                if(original==null) continue;
                var calls=original.GetInvocationList();
                bool changed=false;
                for(int index=0;index<calls.Length;index++)
                {
                    var method=calls[index].Method;
                    if(method.DeclaringType!=typeof(UnityEngine.Input)) continue;
                    MethodInfo replacement;
                    if(!replacements.TryGetValue(Signature(method.Name,method.GetParameters().Select(p=>p.ParameterType.FullName)),out replacement))
                        throw new NotSupportedException("Unmapped existing input delegate: "+method);
                    calls[index]=Delegate.CreateDelegate(field.FieldType,replacement);
                    changed=true;
                }
                if(changed)
                {
                    field.SetValue(null,Delegate.Combine(calls));
                    redirectedDelegates.Add(new KeyValuePair<FieldInfo,Delegate>(field,original));
                }
            }
        }
        private static IEnumerable<TypeDefinition> Types(IEnumerable<TypeDefinition> types)
        {
            foreach(TypeDefinition type in types)
            {
                yield return type;
                foreach(TypeDefinition nested in Types(type.NestedTypes)) yield return nested;
            }
        }
        private static IEnumerable<CodeInstruction> Redirect(IEnumerable<CodeInstruction> instructions)
        {
            foreach(CodeInstruction instruction in instructions)
            {
                var method=instruction.operand as MethodInfo;
                if(method!=null && method.DeclaringType==typeof(UnityEngine.Input))
                {
                    MethodInfo replacement;
                    if(!replacements.TryGetValue(Signature(method.Name,method.GetParameters().Select(p=>p.ParameterType.FullName)),out replacement))
                        throw new NotSupportedException("Unmapped input call: "+method);
                    instruction.operand=replacement;
                }
                yield return instruction;
            }
        }
        public void Dispose()
        {
            foreach(var item in redirectedDelegates) item.Key.SetValue(null,item.Value);
            redirectedDelegates.Clear();
            harmony.UnpatchSelf(); PatchedMethods=0;
        }
    }
}
