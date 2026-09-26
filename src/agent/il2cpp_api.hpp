#pragma once

#include <Windows.h>

#include <cstddef>
#include <cstdint>

struct Il2CppAssembly;
struct Il2CppClass;
struct Il2CppDomain;
struct FieldInfo;
struct Il2CppImage;
struct Il2CppType;
struct MethodInfo;
struct PropertyInfo;
struct Il2CppString;

struct Il2CppApi {
    using DomainGet = Il2CppDomain* (*)();
    using DomainGetAssemblies = const Il2CppAssembly** (*)(const Il2CppDomain*, std::size_t*);
    using AssemblyGetImage = const Il2CppImage* (*)(const Il2CppAssembly*);
    using ImageGetName = const char* (*)(const Il2CppImage*);
    using ImageGetClassCount = std::size_t (*)(const Il2CppImage*);
    using ImageGetClass = Il2CppClass* (*)(const Il2CppImage*, std::size_t);
    using ClassFromName = Il2CppClass* (*)(const Il2CppImage*, const char*, const char*);
    using ClassGetName = const char* (*)(Il2CppClass*);
    using ClassGetNamespace = const char* (*)(Il2CppClass*);
    using ClassGetParent = Il2CppClass* (*)(Il2CppClass*);
    using ClassGetElementClass = Il2CppClass* (*)(Il2CppClass*);
    using ClassValueSize = std::int32_t (*)(Il2CppClass*, std::uint32_t*);
    using ClassGetFields = FieldInfo* (*)(Il2CppClass*, void**);
    using ClassGetMethods = const MethodInfo* (*)(Il2CppClass*, void**);
    using ClassGetProperties = const PropertyInfo* (*)(Il2CppClass*, void**);
    using ClassGetMethodFromName = const MethodInfo* (*)(Il2CppClass*, const char*, int);
    using ClassGetFieldFromName = FieldInfo* (*)(Il2CppClass*, const char*);
    using FieldGetName = const char* (*)(FieldInfo*);
    using FieldGetOffset = std::size_t (*)(FieldInfo*);
    using FieldGetFlags = std::uint32_t (*)(FieldInfo*);
    using FieldGetType = const Il2CppType* (*)(FieldInfo*);
    using FieldStaticGetValue = void (*)(FieldInfo*, void*);
    using RuntimeClassInit = void (*)(Il2CppClass*);
    using MethodGetName = const char* (*)(const MethodInfo*);
    using MethodGetParamCount = std::uint32_t (*)(const MethodInfo*);
    using MethodGetParam = const Il2CppType* (*)(const MethodInfo*, std::uint32_t);
    using MethodGetParamName = const char* (*)(const MethodInfo*, std::uint32_t);
    using MethodGetReturnType = const Il2CppType* (*)(const MethodInfo*);
    using MethodGetObject = void* (*)(const MethodInfo*, Il2CppClass*);
    using MethodGetFromReflection = const MethodInfo* (*)(void*);
    using PropertyGetName = const char* (*)(const PropertyInfo*);
    using PropertyGetGetMethod = const MethodInfo* (*)(const PropertyInfo*);
    using TypeGetName = char* (*)(const Il2CppType*);
    using ClassGetType = const Il2CppType* (*)(Il2CppClass*);
    using TypeGetObject = void* (*)(const Il2CppType*);
    using ObjectGetClass = Il2CppClass* (*)(void*);
    using ArrayNew = void* (*)(Il2CppClass*, std::uintptr_t);
    using ArrayLength = std::uintptr_t (*)(void*);
    using ArrayGetByteLength = std::uintptr_t (*)(void*);
    using ArrayObjectHeaderSize = std::uintptr_t (*)();
    using GcWBarrierSetField = void (*)(void*, void**, void*);
    // Unity 6000 returns a pointer-width handle. Narrowing it to uint32_t
    // discards the upper address bits before il2cpp_gchandle_get_target.
    using GcHandleNew = std::uintptr_t (*)(void*, bool);
    using GcHandleGetTarget = void* (*)(std::uintptr_t);
    using GcHandleFree = void (*)(std::uintptr_t);
    using Free = void (*)(void*);
    using RuntimeInvoke = void* (*)(const MethodInfo*, void*, void**, void**);
    using FormatException = void (*)(void*, char*, int);
    using FormatStackTrace = void (*)(void*, char*, int);
    using ObjectUnbox = void* (*)(void*);
    using ObjectNew = void* (*)(Il2CppClass*);
    using ClassFromType = Il2CppClass* (*)(const Il2CppType*);
    using StringNew = Il2CppString* (*)(const char*);
    using StringLength = std::int32_t (*)(Il2CppString*);
    using StringChars = const wchar_t* (*)(Il2CppString*);
    using ThreadAttach = void* (*)(Il2CppDomain*);
    using ThreadCurrent = void* (*)();
    using ThreadDetach = void (*)(void*);

    HMODULE module{};
    DomainGet domain_get{};
    DomainGetAssemblies domain_get_assemblies{};
    AssemblyGetImage assembly_get_image{};
    ImageGetName image_get_name{};
    ImageGetClassCount image_get_class_count{};
    ImageGetClass image_get_class{};
    ClassFromName class_from_name{};
    ClassGetName class_get_name{};
    ClassGetNamespace class_get_namespace{};
    ClassGetParent class_get_parent{};
    ClassGetElementClass class_get_element_class{};
    ClassValueSize class_value_size{};
    ClassGetFields class_get_fields{};
    ClassGetMethods class_get_methods{};
    ClassGetProperties class_get_properties{};
    ClassGetMethodFromName class_get_method_from_name{};
    ClassGetFieldFromName class_get_field_from_name{};
    FieldGetName field_get_name{};
    FieldGetOffset field_get_offset{};
    FieldGetFlags field_get_flags{};
    FieldGetType field_get_type{};
    FieldStaticGetValue field_static_get_value{};
    RuntimeClassInit runtime_class_init{};
    MethodGetName method_get_name{};
    MethodGetParamCount method_get_param_count{};
    MethodGetParam method_get_param{};
    MethodGetParamName method_get_param_name{};
    MethodGetReturnType method_get_return_type{};
    MethodGetObject method_get_object{};
    MethodGetFromReflection method_get_from_reflection{};
    PropertyGetName property_get_name{};
    PropertyGetGetMethod property_get_get_method{};
    TypeGetName type_get_name{};
    ClassGetType class_get_type{};
    TypeGetObject type_get_object{};
    ObjectGetClass object_get_class{};
    ArrayNew array_new{};
    ArrayLength array_length{};
    ArrayGetByteLength array_get_byte_length{};
    ArrayObjectHeaderSize array_object_header_size{};
    GcWBarrierSetField gc_wbarrier_set_field{};
    GcHandleNew gchandle_new{};
    GcHandleGetTarget gchandle_get_target{};
    GcHandleFree gchandle_free{};
    Free free{};
    RuntimeInvoke runtime_invoke{};
    FormatException format_exception{};
    FormatStackTrace format_stack_trace{};
    ObjectUnbox object_unbox{};
    // Optional: only the prepared-team preview uses these.
    ObjectNew object_new{};
    ClassFromType class_from_type{};
    StringNew string_new{};
    StringLength string_length{};
    StringChars string_chars{};
    ThreadAttach thread_attach{};
    ThreadCurrent thread_current{};
    ThreadDetach thread_detach{};

    template <typename T>
    static T resolve(HMODULE source, const char* name) {
        return reinterpret_cast<T>(GetProcAddress(source, name));
    }

    bool load(HMODULE game_assembly) {
        module = game_assembly;
        domain_get = resolve<DomainGet>(module, "il2cpp_domain_get");
        domain_get_assemblies =
            resolve<DomainGetAssemblies>(module, "il2cpp_domain_get_assemblies");
        assembly_get_image = resolve<AssemblyGetImage>(module, "il2cpp_assembly_get_image");
        image_get_name = resolve<ImageGetName>(module, "il2cpp_image_get_name");
        image_get_class_count =
            resolve<ImageGetClassCount>(module, "il2cpp_image_get_class_count");
        image_get_class = resolve<ImageGetClass>(module, "il2cpp_image_get_class");
        class_from_name = resolve<ClassFromName>(module, "il2cpp_class_from_name");
        class_get_name = resolve<ClassGetName>(module, "il2cpp_class_get_name");
        class_get_namespace =
            resolve<ClassGetNamespace>(module, "il2cpp_class_get_namespace");
        class_get_parent = resolve<ClassGetParent>(module, "il2cpp_class_get_parent");
        class_get_element_class = resolve<ClassGetElementClass>(
            module, "il2cpp_class_get_element_class");
        class_value_size =
            resolve<ClassValueSize>(module, "il2cpp_class_value_size");
        class_get_fields = resolve<ClassGetFields>(module, "il2cpp_class_get_fields");
        class_get_methods = resolve<ClassGetMethods>(module, "il2cpp_class_get_methods");
        class_get_properties =
            resolve<ClassGetProperties>(module, "il2cpp_class_get_properties");
        class_get_method_from_name = resolve<ClassGetMethodFromName>(
            module, "il2cpp_class_get_method_from_name");
        class_get_field_from_name = resolve<ClassGetFieldFromName>(
            module, "il2cpp_class_get_field_from_name");
        field_get_name = resolve<FieldGetName>(module, "il2cpp_field_get_name");
        field_get_offset = resolve<FieldGetOffset>(module, "il2cpp_field_get_offset");
        field_get_flags = resolve<FieldGetFlags>(module, "il2cpp_field_get_flags");
        field_get_type = resolve<FieldGetType>(module, "il2cpp_field_get_type");
        field_static_get_value = resolve<FieldStaticGetValue>(
            module, "il2cpp_field_static_get_value");
        runtime_class_init = resolve<RuntimeClassInit>(
            module, "il2cpp_runtime_class_init");
        method_get_name = resolve<MethodGetName>(module, "il2cpp_method_get_name");
        method_get_param_count =
            resolve<MethodGetParamCount>(module, "il2cpp_method_get_param_count");
        method_get_param = resolve<MethodGetParam>(module, "il2cpp_method_get_param");
        method_get_param_name =
            resolve<MethodGetParamName>(module, "il2cpp_method_get_param_name");
        method_get_return_type =
            resolve<MethodGetReturnType>(module, "il2cpp_method_get_return_type");
        method_get_object = resolve<MethodGetObject>(module, "il2cpp_method_get_object");
        method_get_from_reflection = resolve<MethodGetFromReflection>(
            module, "il2cpp_method_get_from_reflection");
        property_get_name = resolve<PropertyGetName>(module, "il2cpp_property_get_name");
        property_get_get_method =
            resolve<PropertyGetGetMethod>(module, "il2cpp_property_get_get_method");
        type_get_name = resolve<TypeGetName>(module, "il2cpp_type_get_name");
        class_get_type = resolve<ClassGetType>(module, "il2cpp_class_get_type");
        type_get_object = resolve<TypeGetObject>(module, "il2cpp_type_get_object");
        object_get_class = resolve<ObjectGetClass>(module, "il2cpp_object_get_class");
        array_new = resolve<ArrayNew>(module, "il2cpp_array_new");
        array_length = resolve<ArrayLength>(module, "il2cpp_array_length");
        array_get_byte_length = resolve<ArrayGetByteLength>(
            module, "il2cpp_array_get_byte_length");
        array_object_header_size = resolve<ArrayObjectHeaderSize>(
            module, "il2cpp_array_object_header_size");
        gc_wbarrier_set_field = resolve<GcWBarrierSetField>(
            module, "il2cpp_gc_wbarrier_set_field");
        gchandle_new = resolve<GcHandleNew>(module, "il2cpp_gchandle_new");
        gchandle_get_target = resolve<GcHandleGetTarget>(
            module, "il2cpp_gchandle_get_target");
        gchandle_free = resolve<GcHandleFree>(module, "il2cpp_gchandle_free");
        free = resolve<Free>(module, "il2cpp_free");
        runtime_invoke = resolve<RuntimeInvoke>(module, "il2cpp_runtime_invoke");
        format_exception =
            resolve<FormatException>(module, "il2cpp_format_exception");
        format_stack_trace =
            resolve<FormatStackTrace>(module, "il2cpp_format_stack_trace");
        object_unbox = resolve<ObjectUnbox>(module, "il2cpp_object_unbox");
        object_new = resolve<ObjectNew>(module, "il2cpp_object_new");
        class_from_type = resolve<ClassFromType>(module, "il2cpp_class_from_il2cpp_type");
        string_new = resolve<StringNew>(module, "il2cpp_string_new");
        string_length = resolve<StringLength>(module, "il2cpp_string_length");
        string_chars = resolve<StringChars>(module, "il2cpp_string_chars");
        thread_attach = resolve<ThreadAttach>(module, "il2cpp_thread_attach");
        thread_current = resolve<ThreadCurrent>(module, "il2cpp_thread_current");
        thread_detach = resolve<ThreadDetach>(module, "il2cpp_thread_detach");

        return domain_get && domain_get_assemblies && assembly_get_image && image_get_name &&
               image_get_class_count && image_get_class && class_from_name && class_get_name &&
               class_get_namespace && class_get_parent && class_get_element_class &&
               class_value_size && class_get_fields &&
               class_get_methods && class_get_properties && class_get_method_from_name &&
               field_get_name && field_get_offset && field_get_flags && field_get_type &&
               field_static_get_value && method_get_name &&
               method_get_param_count && method_get_param && method_get_param_name &&
               method_get_return_type && property_get_name && property_get_get_method &&
               type_get_name && free && runtime_invoke && object_unbox && string_length &&
               string_new && string_chars && thread_attach && thread_current && thread_detach;
    }
};
