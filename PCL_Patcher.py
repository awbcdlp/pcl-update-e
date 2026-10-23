# -*- coding: utf-8 -*-
"""
PCL2 下载功能恢复补丁工具
功能：
  1. 对 PCL2 2.99+ 版本恢复 Minecraft 游戏本体下载功能
  2. 安装持久化守护，PCL 更新后自动重新打补丁
支持：编译后的 exe（直接二进制 IL 修改）和 VB 源码
"""

import os
import sys
import shutil
import struct
import string
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

# ============================================================
# .NET 元数据解析 + IL 补丁
# ============================================================

# 元数据表 ID
T_MODULE=0x00; T_TYPEREF=0x01; T_TYPEDEF=0x02; T_FIELD=0x04
T_METHODDEF=0x06; T_PARAM=0x08; T_MEMBERREF=0x0A; T_CONSTANT=0x0B
T_CUSTOMATTR=0x0C; T_STANDALONESIG=0x11; T_EVENTMAP=0x12
T_EVENT=0x14; T_PROPERTYMAP=0x15; T_PROPERTY=0x17
T_ASSEMBLY=0x20; T_ASSEMBLYREF=0x23; T_NESTEDCLASS=0x29
T_GENERICPARAM=0x2A; T_METHODSPEC=0x2B

# 各表的列（列类型，不含索引大小，后续动态计算）
TABLE_COLS = {
    T_MODULE:      [('Generation',2),('Name','S'),('Mvid','G'),('EncId','G'),('EncBaseId','G')],
    T_TYPEREF:     [('ResolutionScope','coded:ResolutionScope'),('Name','S'),('Namespace','S')],
    T_TYPEDEF:     [('Flags',4),('Name','S'),('Namespace','S'),('Extends','coded:TypeDefOrRef'),
                    ('FieldList','idx:Field'),('MethodList','idx:MethodDef')],
    0x09:          [('Class','idx:TypeDef'),('Interface','coded:TypeDefOrRef')],  # InterfaceImpl
    T_FIELD:       [('Flags',2),('Name','S'),('Signature','B')],
    T_METHODDEF:   [('RVA',4),('ImplFlags',2),('Flags',2),('Name','S'),('Signature','B'),('ParamList','idx:Param')],
    T_PARAM:       [('Flags',2),('Sequence',2),('Name','S')],
    T_MEMBERREF:   [('Class','coded:MemberRefParent'),('Name','S'),('Signature','B')],
    T_CONSTANT:    [('Type',1),('Padding',1),('Parent','coded:HasConstant'),('Value','B')],
    T_CUSTOMATTR:  [('Parent','coded:HasCustomAttribute'),('Type','coded:CustomAttributeType'),('Value','B')],
    0x0D:          [('Parent','coded:HasFieldMarshal'),('NativeType','B')],  # FieldMarshal
    0x0E:          [('Action',2),('Parent','coded:HasDeclSecurity'),('PermissionSet','B')],  # DeclSecurity
    0x0F:          [('PackingSize',2),('ClassSize',4),('Parent','idx:TypeDef')],  # ClassLayout
    0x10:          [('Offset',4),('Field','idx:Field')],  # FieldLayout
    T_STANDALONESIG:[('Signature','B')],
    T_EVENTMAP:    [('Parent','idx:TypeDef'),('EventList','idx:Event')],
    T_EVENT:       [('EventFlags',2),('Name','S'),('EventType','coded:TypeDefOrRef')],
    T_PROPERTYMAP: [('Parent','idx:TypeDef'),('PropertyList','idx:Property')],
    T_PROPERTY:    [('Flags',2),('Name','S'),('Type','B')],
    0x18:          [('Semantic',2),('Method','idx:MethodDef'),('Association','coded:HasSemantics')],  # MethodSemantics
    0x19:          [('Class','idx:TypeDef'),('MethodBody','coded:MethodDefOrRef'),
                    ('MethodDeclaration','coded:MethodDefOrRef')],  # MethodImpl
    0x1A:          [('Name','S')],  # ModuleRef
    0x1B:          [('Signature','B')],  # TypeSpec
    0x1C:          [('MappingFlags',2),('MemberForwarded','coded:MemberForwarded'),
                    ('ImportName','S'),('ImportScope','idx:ModuleRef')],  # ImplMap
    0x1D:          [('RVA',4),('Field','idx:Field')],  # FieldRVA
    T_ASSEMBLY:    [('HashAlgId',4),('MajorVersion',2),('MinorVersion',2),('BuildNumber',2),
                    ('RevisionNumber',2),('Flags',4),('PublicKey','B'),('Name','S'),('Culture','S')],
    0x21:          [('Processor',4)],  # AssemblyProcessor
    0x22:          [('OSPlatformID',4),('OSMajorVersion',4),('OSMinorVersion',4)],  # AssemblyOS
    T_ASSEMBLYREF: [('MajorVersion',2),('MinorVersion',2),('BuildNumber',2),('RevisionNumber',2),
                    ('Flags',4),('PublicKeyOrToken','B'),('Name','S'),('Culture','S'),('HashValue','B')],
    0x24:          [('Processor',4),('AssemblyRef','idx:AssemblyRef')],  # AssemblyRefProcessor
    0x25:          [('OSPlatformID',4),('OSMajorVersion',4),('OSMinorVersion',4),
                    ('AssemblyRef','idx:AssemblyRef')],  # AssemblyRefOS
    0x26:          [('Flags',4),('Name','S'),('HashValue','B')],  # File
    0x27:          [('Flags',4),('TypeDefId',4),('TypeName','S'),('TypeNamespace','S'),
                    ('Implementation','coded:Implementation')],  # ExportedType
    0x28:          [('Offset',4),('Flags',4),('Name','S'),
                    ('Implementation','coded:Implementation')],  # ManifestResource
    T_NESTEDCLASS: [('NestedClass','idx:TypeDef'),('EnclosingClass','idx:TypeDef')],
    T_GENERICPARAM:[('Number',2),('Flags',2),('Owner','coded:TypeOrMethodDef'),('Name','S')],
    T_METHODSPEC:  [('Method','coded:MethodDefOrRef'),('Instantiation','B')],
    0x2C:          [('Owner','idx:GenericParam'),('Constraint','coded:TypeDefOrRef')],  # GenericParamConstraint
}

# Coded index 定义：(tag_bits, [表ID列表])
CODED_INDEX = {
    'TypeDefOrRef': (2, [T_TYPEDEF, T_TYPEREF, 0x1B]),
    'HasConstant': (2, [T_FIELD, T_PARAM, T_PROPERTY]),
    'HasCustomAttribute': (5, [0x00,T_TYPEREF,T_TYPEDEF,T_FIELD,T_METHODDEF,T_PARAM,
                               0x09,0x0A,0x0B,0x14,0x17,0x20,0x23,0x26,0x27,0x28,0x2A,0x2C]),
    'CustomAttributeType': (3, [0x06,0x0A]),
    'ResolutionScope': (2, [T_MODULE,T_TYPEREF,0x1A,0x23]),
    'MemberRefParent': (3, [T_TYPEDEF,T_TYPEREF,0x1B,0x0A,0x1A]),
    'TypeOrMethodDef': (1, [T_TYPEDEF,T_METHODDEF]),
    'MethodDefOrRef': (1, [T_METHODDEF,T_MEMBERREF]),
    'HasFieldMarshal': (1, [T_FIELD, T_PARAM]),
    'HasDeclSecurity': (2, [T_TYPEDEF, T_METHODDEF, T_ASSEMBLY]),
    'HasSemantics': (1, [T_EVENT, T_PROPERTY]),
    'MemberForwarded': (1, [T_FIELD, T_METHODDEF]),
    'Implementation': (2, [0x26, T_ASSEMBLYREF, 0x27]),
}

class DotNetAssembly:
    """解析 .NET PE 程序集，定位方法并补丁 IL"""
    def __init__(self, data: bytes):
        self.raw = bytearray(data)
        self._parse()

    def _u16(self, off): return struct.unpack_from('<H', self.raw, off)[0]
    def _u32(self, off): return struct.unpack_from('<I', self.raw, off)[0]

    def _rva_to_off(self, rva):
        for sec in self.sections:
            va, vsz, raw_off, raw_sz = sec
            if va <= rva < va + max(vsz, raw_sz):
                return raw_off + (rva - va)
        return None

    def _parse(self):
        d = self.raw
        pe_off = self._u32(0x3C)
        assert d[pe_off:pe_off+4] == b'PE\x00\x00', "非 PE 文件"
        coff = pe_off + 4
        num_sections = self._u16(coff + 2)
        opt_size = self._u16(coff + 16)
        opt_off = coff + 20
        magic = self._u16(opt_off)
        # 数据目录
        if magic == 0x10b:  # PE32
            dd_off = opt_off + 96
        else:              # PE32+
            dd_off = opt_off + 112
        # CLI Header = 第15个目录(index 14)
        cli_rva = self._u32(dd_off + 14*8)
        cli_size = self._u32(dd_off + 14*8 + 4)
        # 节表
        sec_off = opt_off + opt_size
        self.sections = []
        for i in range(num_sections):
            s = sec_off + i*40
            vsize = self._u32(s+8); va = self._u32(s+12)
            raw_sz = self._u32(s+16); raw_off = self._u32(s+20)
            self.sections.append((va, vsize, raw_off, raw_sz))
        cli_off = self._rva_to_off(cli_rva)
        assert cli_off is not None, "找不到 CLI 头"
        meta_rva = self._u32(cli_off + 8)
        meta_size = self._u32(cli_off + 12)
        self.meta_off = self._rva_to_off(meta_rva)
        assert self.meta_off is not None, "找不到元数据"
        self._parse_metadata()

    def _parse_metadata(self):
        off = self.meta_off
        d = self.raw
        assert d[off:off+4] == b'BSJB', "元数据签名错误"
        ver_len = self._u32(off + 12)
        p = off + 16 + ver_len
        # 版本字符串 4 字节对齐
        if ver_len % 4: p += 4 - (ver_len % 4)
        p += 2  # flags
        num_streams = self._u16(p); p += 2
        self.streams = {}
        for _ in range(num_streams):
            so = self._u32(p); ss = self._u32(p+4); p += 8
            name_start = p
            while d[p] != 0: p += 1
            name = d[name_start:p].decode('ascii', 'replace')
            p += 1
            while p % 4: p += 1
            self.streams[name] = (self.meta_off + so, ss)

        # 堆
        s_off, s_sz = self.streams['#Strings']
        self.strings = bytes(d[s_off:s_off+s_sz])
        b_off, b_sz = self.streams['#Blob']
        self.blob = bytes(d[b_off:b_off+b_sz])
        g_off, g_sz = self.streams['#GUID']
        self.guid = bytes(d[g_off:g_off+g_sz])
        t_off, t_sz = self.streams['#~'] if '#~' in self.streams else self.streams['#-']
        self.tilde_off = t_off
        self._parse_tilde(t_off)

    def _read_string(self, idx):
        if idx == 0: return ""
        end = self.strings.index(b'\x00', idx)
        return self.strings[idx:end].decode('utf-8', 'replace')

    def _parse_tilde(self, off):
        d = self.raw
        p = off + 4  # reserved
        p += 2      # version
        heap_sizes = d[p]; p += 1
        p += 1      # reserved
        self.valid = struct.unpack_from('<Q', d, p)[0]; p += 8
        sorted_mask = struct.unpack_from('<Q', d, p)[0]; p += 8
        # 计算存在的表
        self.present_tables = [i for i in range(64) if (self.valid >> i) & 1]
        # 读行数
        self.row_counts = {}
        for t in self.present_tables:
            self.row_counts[t] = self._u32(p); p += 4

        # 堆索引大小
        self.string_idx_size = 4 if (heap_sizes & 1) else 2
        self.guid_idx_size = 4 if (heap_sizes & 2) else 2
        self.blob_idx_size = 4 if (heap_sizes & 4) else 2

        # 计算每种索引大小
        def idx_size(table_id):
            return 4 if self.row_counts.get(table_id, 0) > 65535 else 2

        def coded_size(name):
            bits, tables = CODED_INDEX[name]
            max_rows = max(self.row_counts.get(t, 0) for t in tables)
            return 4 if max_rows >= (1 << (16 - bits)) else 2

        self.idx_size = idx_size
        self.coded_size = coded_size

        # 计算每表行大小
        idx_map = {'Field':T_FIELD,'MethodDef':T_METHODDEF,'Param':T_PARAM,
                   'Event':T_EVENT,'Property':T_PROPERTY,'TypeDef':T_TYPEDEF,
                   'ModuleRef':0x1A,'AssemblyRef':T_ASSEMBLYREF,'GenericParam':T_GENERICPARAM}
        self.table_row_size = {}
        self.table_offsets = {}
        for t in self.present_tables:
            cols = TABLE_COLS.get(t)
            if cols is None:
                raise Exception(f"不支持的表 0x{t:02X}")
            row_sz = 0
            for _, ct in cols:
                if isinstance(ct, int):
                    row_sz += ct
                elif ct == 'S':
                    row_sz += self.string_idx_size
                elif ct == 'G':
                    row_sz += self.guid_idx_size
                elif ct == 'B':
                    row_sz += self.blob_idx_size
                elif ct.startswith('idx:'):
                    row_sz += idx_size(idx_map[ct[4:]])
                elif ct.startswith('coded:'):
                    row_sz += coded_size(ct[6:])
            self.table_row_size[t] = row_sz
            self.table_offsets[t] = p
            p += row_sz * self.row_counts[t]

    def _read_table_cell(self, table_id, row_index, col_index):
        """读取某行某列的值（0-based row_index）"""
        cols = TABLE_COLS[table_id]
        row_base = self.table_offsets[table_id] + row_index * self.table_row_size[table_id]
        idx_map = {'Field':T_FIELD,'MethodDef':T_METHODDEF,'Param':T_PARAM,
                   'Event':T_EVENT,'Property':T_PROPERTY,'TypeDef':T_TYPEDEF,
                   'ModuleRef':0x1A,'AssemblyRef':T_ASSEMBLYREF,'GenericParam':T_GENERICPARAM}
        p = row_base
        for i in range(col_index + 1):
            _, ct = cols[i]
            if isinstance(ct, int):
                if ct == 1: val = self.raw[p]
                elif ct == 2: val = self._u16(p)
                elif ct == 4: val = self._u32(p)
                p += ct
            elif ct == 'S':
                sz = self.string_idx_size
                val = self._u32(p) if sz == 4 else self._u16(p)
                p += sz
            elif ct == 'G':
                sz = self.guid_idx_size
                val = self._u32(p) if sz == 4 else self._u16(p)
                p += sz
            elif ct == 'B':
                sz = self.blob_idx_size
                val = self._u32(p) if sz == 4 else self._u16(p)
                p += sz
            elif ct.startswith('idx:'):
                sz = self.idx_size(idx_map[ct[4:]])
                val = self._u32(p) if sz == 4 else self._u16(p)
                p += sz
            elif ct.startswith('coded:'):
                cname = ct[6:]
                sz = self.coded_size(cname)
                val = self._u32(p) if sz == 4 else self._u16(p)
                p += sz
        return val

    def find_type_methods(self, type_name):
        """返回 (类型索引, 方法起始1-based行, 方法结束1-based行)"""
        n_td = self.row_counts.get(T_TYPEDEF, 0)
        for i in range(n_td):
            name_idx = self._read_table_cell(T_TYPEDEF, i, 1)
            ns_idx = self._read_table_cell(T_TYPEDEF, i, 2)
            name = self._read_string(name_idx)
            ns = self._read_string(ns_idx)
            if name == type_name:
                m_start = self._read_table_cell(T_TYPEDEF, i, 5)
                m_end = self.row_counts.get(T_METHODDEF, 0) + 1
                if i + 1 < n_td:
                    m_end = self._read_table_cell(T_TYPEDEF, i+1, 5)
                return i, m_start, m_end
        return None

    def find_method_rva(self, type_name, method_name):
        """找到类型中指定名称的所有方法的 RVA（1-based 行号转 RVA）"""
        res = self.find_type_methods(type_name)
        if not res: return []
        _, m_start, m_end = res
        rvas = []
        for m in range(m_start, m_end):
            row = m - 1
            name_idx = self._read_table_cell(T_METHODDEF, row, 3)
            name = self._read_string(name_idx)
            if name == method_name:
                rva = self._read_table_cell(T_METHODDEF, row, 0)
                rvas.append((m, rva))
        return rvas

    def _read_method_body(self, rva):
        """返回 (header_size, il_off, il_size, fat)"""
        off = self._rva_to_off(rva)
        if off is None: return None
        first = self.raw[off]
        if (first & 3) == 2:  # Tiny
            code_size = first >> 2
            return 1, off + 1, code_size, False
        elif (first & 3) == 3:  # Fat
            # Fat header: Flags(2) MaxStack(2) CodeSize(4) LocalVarTok(4)
            code_size = self._u32(off + 4)
            return 12, off + 12, code_size, True
        return None

    def patch_method_il(self, rva, patches):
        """
        patches: [(il_offset, old_bytes, new_bytes), ...]
        在方法 IL 中执行精确替换
        """
        body = self._read_method_body(rva)
        if not body: return False
        _, il_off, il_size, _ = body
        for il_rel_off, old, new in patches:
            abs_off = il_off + il_rel_off
            cur = bytes(self.raw[abs_off:abs_off+len(old)])
            if cur == old:
                self.raw[abs_off:abs_off+len(new)] = new
            else:
                return False
        return True

    def search_il_in_method(self, rva, pattern, occurrence=0):
        """在方法 IL 中搜索字节模式，返回第 occurrence 个匹配的 IL 偏移"""
        body = self._read_method_body(rva)
        if not body: return -1
        _, il_off, il_size, _ = body
        il = bytes(self.raw[il_off:il_off+il_size])
        count = 0
        start = 0
        while True:
            idx = il.find(pattern, start)
            if idx < 0: return -1
            if count == occurrence:
                return idx
            count += 1
            start = idx + 1

    def _find_field_token(self, field_name):
        """按名字查找字段 token"""
        n = self.row_counts.get(T_FIELD, 0)
        for i in range(n):
            name_idx = self._read_table_cell(T_FIELD, i, 1)
            if self._read_string(name_idx) == field_name:
                return 0x04000000 + i + 1
        return None

    def is_already_patched(self):
        """检查 LoaderInit 是否已补丁：不再包含"关闭下载"字符串引用"""
        for _, rva in self.find_method_rva('PageDownloadInstall', 'LoaderInit'):
            body = self._read_method_body(rva)
            if not body: continue
            _, il_off, il_size, _ = body
            il = bytes(self.raw[il_off:il_off+il_size])
            # 已补丁：直接 ldsfld DlClientListLoader（7E），且没有 ldstr "关闭下载"
            has_ldstr = False
            for i in range(len(il) - 4):
                if il[i] == 0x72:
                    has_ldstr = True
                    break
            # 假 LoaderTask 模式：ldstr + ldsfld + brfalse.s
            if b'\x72' in il and b'\x2c' in il and b'\x7e' in il:
                # 检查是否有 ldstr+ldsfld+brfalse 这个特征（委托缓存+关闭下载）
                for i in range(len(il)-11):
                    if il[i] == 0x72 and il[i+5] == 0x7E and il[i+10] == 0x2C:
                        return False
            return True
        return False

    def apply_patch(self):
        """
        应用全部 IL 补丁（基于特征码，跨版本健壮）
        补丁点：
          1. LoaderInit：把假 LoaderTask 构造替换为 ldsfld DlClientListLoader
          2. LoadInstanceFinished：CardMinecraft.Visibility Collapsed -> Visible
          3. ModpackInstall 有参 Function：移除末尾的限制 throw
          4. ModpackInstall 无参 Sub：被折叠为 Hint，整个方法改为直接 ret
        """
        patched = 0
        details = []

        # ---------- 补丁 1：LoaderInit ----------
        dl_loader_token = self._find_field_token('DlClientListLoader')
        for _, rva in self.find_method_rva('PageDownloadInstall', 'LoaderInit'):
            body = self._read_method_body(rva)
            if not body: continue
            _, il_off, il_size, _ = body
            il = bytes(self.raw[il_off:il_off+il_size])
            # 特征：ldstr(72) + token(4) + ldsfld(7E) + token(4) + brfalse.s(2C)
            # 这是假 LoaderTask 的委托缓存 + 字符串
            start = -1
            for i in range(len(il) - 11):
                if (il[i] == 0x72 and il[i+5] == 0x7E and il[i+10] == 0x2C):
                    start = i
                    break
            if start < 0:
                details.append("  [LoaderInit] 未找到假 LoaderTask 特征（可能已补丁）")
                continue
            # 找 start 之后的第二个 newobj（LoaderTask 构造）：
            # 第一个 newobj 是 Action 委托（73 ... 0A），第二个是 LoaderTask（前缀 ldnull 14 + ldc.i4.2 18）
            end = -1
            j = start
            while j < len(il) - 5:
                if il[j] == 0x73 and j >= 2 and il[j-1] == 0x18 and il[j-2] == 0x14:
                    end = j + 5
                    break
                j += 1
            if end < 0:
                details.append("  [LoaderInit] 未找到 LoaderTask 构造")
                continue
            # 用 ldsfld DlClientListLoader 替换整个构造代码
            if dl_loader_token is None:
                details.append("  [LoaderInit] 找不到 DlClientListLoader 字段")
                continue
            ldsfld = bytes([0x7E]) + struct.pack('<I', dl_loader_token)
            region = end - start
            new_bytes = ldsfld + b'\x00' * (region - len(ldsfld))
            self.raw[il_off + start: il_off + end] = new_bytes
            patched += 1
            details.append(f"  [成功] LoaderInit: IL_{start:04X}-IL_{end:04X} 恢复为 DlClientListLoader（{region}字节）")

        # ---------- 补丁 2：LoadInstanceFinished（CardMinecraft 可见性）----------
        # 找 get_CardMinecraft 方法 token
        card_getter = None
        # 搜索 PageDownloadInstall 中的 get_CardMinecraft
        for _, rva in self.find_method_rva('PageDownloadInstall', 'get_CardMinecraft'):
            # 找它的 token
            res = self.find_type_methods('PageDownloadInstall')
            _, m_start, m_end = res
            for m in range(m_start, m_end):
                name = self._read_string(self._read_table_cell(T_METHODDEF, m-1, 3))
                if name == 'get_CardMinecraft':
                    card_getter = 0x06000000 + m
                    break
        for _, rva in self.find_method_rva('PageDownloadModify', 'LoadInstanceFinished'):
            body = self._read_method_body(rva)
            if not body: continue
            _, il_off, il_size, _ = body
            il = bytes(self.raw[il_off:il_off+il_size])
            # 特征：call/callvirt get_CardMinecraft + ldc.i4.2(18) + callvirt set_Visibility
            count = 0
            if card_getter:
                getter_call = bytes([0x28]) + struct.pack('<I', card_getter)
                getter_virt = bytes([0x6F]) + struct.pack('<I', card_getter)
                pos = 0
                while True:
                    gi = -1
                    for pat in (getter_call, getter_virt):
                        gi = il.find(pat, pos)
                        if gi >= 0: break
                    if gi < 0: break
                    after = gi + 5
                    # 后面应该直接跟 ldc.i4.2 (0x18) 然后 set_Visibility
                    if after < len(il) and il[after] == 0x18:
                        self.raw[il_off + after] = 0x16  # Collapsed -> Visible
                        patched += 1
                        count += 1
                        details.append(f"  [成功] LoadInstanceFinished: IL_{after:04X} CardMinecraft 可见性恢复")
                    pos = after + 1
            else:
                details.append("  [LoadInstanceFinished] 未找到 get_CardMinecraft")

        # ---------- 补丁 3：ModpackInstall 有参 Function ----------
        # 找末尾的限制 throw 块：ldstr + newobj + throw（7A）
        methods = self.find_method_rva('ModModpack', 'ModpackInstall')
        for row, rva in methods:
            body = self._read_method_body(rva)
            if not body: continue
            _, il_off, il_size, _ = body
            if il_size < 100: continue  # 跳过无参 Sub
            il = bytes(self.raw[il_off:il_off+il_size])
            # 从方法末尾向前搜索 throw(7A)，检查前面是 newobj(73)+ldstr(72)
            # 限制块：72 token 73 token 7A  = 11 字节
            target = -1
            for i in range(len(il) - 11, 0, -1):
                if (il[i] == 0x72 and il[i+5] == 0x73 and il[i+10] == 0x7A):
                    target = i
                    break
            if target < 0:
                details.append("  [ModpackInstall 有参] 未找到限制 throw（可能已补丁）")
                continue
            # 替换为 ldnull + ret + nop（leave 跳到这里后正常返回）
            new_bytes = b'\x14\x2A' + b'\x00' * 9  # ldnull; ret; nop*9
            self.raw[il_off + target: il_off + target + 11] = new_bytes
            patched += 1
            details.append(f"  [成功] ModpackInstall 有参: IL_{target:04X} 限制 throw 移除（11字节）")

        # ---------- 补丁 4：ModpackInstall 无参 Sub ----------
        # 被折叠为：newobj ...; stloc.0; ldstr; ldc.i4.2; ldc.i4.1; call Hint; ret
        # 特征：方法很短(<30字节)，包含 Hint 调用，不含 SelectFile
        for row, rva in methods:
            body = self._read_method_body(rva)
            if not body: continue
            _, il_off, il_size, _ = body
            if il_size >= 30: continue
            il = bytes(self.raw[il_off:il_off+il_size])
            # 这个方法不应该直接 ret（会让按钮无反应），但也不能弹 Hint
            # 替换为：ret（空方法体），nop 填充
            if il[-1] == 0x2A and il_size >= 5:
                new_bytes = b'\x2A' + b'\x00' * (il_size - 1)
                self.raw[il_off: il_off + il_size] = new_bytes
                patched += 1
                details.append(f"  [成功] ModpackInstall 无参: 移除限制提示（{il_size}字节，方法保留为空）")

        return patched, details


# ============================================================
# 补丁逻辑
# ============================================================

EXE_NAME = "Plain Craft Launcher 2.exe"
RESTRICT_MARKER = b"RestrictMcDownload"
RESTRICT_MSG = "为遵守相关法律法规，PCL 暂时不再提供 MC 下载功能".encode('utf-8')


def find_pcl_exe_in_folder(folder):
    """在文件夹中找到 PCL exe（递归，最多2层）"""
    direct = os.path.join(folder, EXE_NAME)
    if os.path.isfile(direct):
        return direct
    # 递归一层
    for root, dirs, files in os.walk(folder):
        depth = root[len(folder):].count(os.sep)
        if depth >= 2:
            dirs.clear()
            continue
        for f in files:
            if f.lower() == EXE_NAME.lower():
                return os.path.join(root, f)
    return None


def is_pcl_exe(path):
    """检查文件是否是 PCL（通过特征字符串）"""
    try:
        with open(path, 'rb') as f:
            data = f.read()
        # 检查 .NET + PCL 特征
        if b'BSJB' not in data:
            return False
        # 检查 PCL 特征字符串
        markers = [b'Plain Craft Launcher', b'PCL', b'PageDownloadInstall']
        score = sum(1 for m in markers if m in data)
        return score >= 2
    except Exception:
        return False


def is_source_folder(folder):
    """判断是否是 PCL 源码文件夹"""
    return os.path.isfile(os.path.join(folder, "Plain Craft Launcher 2.sln")) or \
           os.path.isdir(os.path.join(folder, "Plain Craft Launcher 2", "Pages"))


def patch_source(folder, log):
    """对源码文件夹打补丁"""
    # 找 ModSecret.vb
    candidates = []
    for root, dirs, files in os.walk(folder):
        if 'ModSecret.vb' in files:
            candidates.append(os.path.join(root, 'ModSecret.vb'))
    if not candidates:
        return False, "未找到 ModSecret.vb"
    count = 0
    for path in candidates:
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
            original = content
            # 替换 True 为 False
            import re
            content = re.sub(
                r'(RestrictMcDownload\s+As\s+Boolean\s*=\s*)True',
                r'\1False', content)
            if content != original:
                # 备份
                bak = path + '.bak'
                if not os.path.exists(bak):
                    shutil.copy2(path, bak)
                with open(path, 'w', encoding='utf-8-sig') as f:
                    f.write(content)
                count += 1
                log(f"  [源码] 已修改: {path}")
        except Exception as e:
            log(f"  [错误] {path}: {e}")
    return count > 0, f"修改了 {count} 个 ModSecret.vb"


def patch_exe(path, log):
    """对编译后的 exe 打补丁"""
    try:
        with open(path, 'rb') as f:
            data = f.read()

        # 检查是否是 2.99+ 版本
        if RESTRICT_MARKER not in data:
            log("  该文件不包含 RestrictMcDownload 限制，无需补丁（可能已是旧版本）")
            return True, "无需补丁"

        asm = DotNetAssembly(data)

        # 检查是否已补丁
        if asm.is_already_patched():
            log("  该文件已经打过补丁，跳过")
            return True, "已补丁"

        # 备份
        bak = path + '.bak'
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            log(f"  已备份原文件: {os.path.basename(bak)}")

        n, details = asm.apply_patch()
        for d in details:
            log(d)

        if n == 0:
            return False, "未找到可补丁的 IL 位置（可能版本结构不同）"

        # 保存
        with open(path, 'wb') as f:
            f.write(bytes(asm.raw))
        log(f"  共修改 {n} 处 IL 指令")
        return True, f"成功补丁 {n} 处"
    except Exception as e:
        import traceback
        log("  异常: " + str(e))
        log(traceback.format_exc())
        return False, str(e)


def patch_pcl_folder(folder, log):
    """对一个 PCL 文件夹/文件打补丁，返回 (是否成功, 数量)"""
    if os.path.isfile(folder):
        # 直接是 exe
        if not is_pcl_exe(folder):
            return False, 0
        return patch_exe(folder, log)

    # 源码？
    if is_source_folder(folder):
        log("检测到源码文件夹")
        return patch_source(folder, log)

    # 编译后的 exe
    exe_path = find_pcl_exe_in_folder(folder)
    if exe_path:
        log(f"找到: {exe_path}")
        return patch_exe(exe_path, log)

    return False, "未找到 PCL 文件（Plain Craft Launcher 2.exe 或源码）"


# ============================================================
# 磁盘搜索
# ============================================================

def get_drives():
    drives = []
    for c in string.ascii_uppercase:
        d = c + ':\\'
        if os.path.exists(d):
            drives.append(d)
    return drives


def search_drive(drive, callback, stop_flag):
    """在单个盘符搜索 PCL：先常见目录，再全盘"""
    found = []
    seen = set()
    skip_dirs = {'Windows', '$Recycle.Bin', 'System Volume Information', '$WinREAgent',
                 'node_modules', '.git', 'ProgramData\\Package Cache'}

    def scan_path(base, label):
        if not os.path.isdir(base):
            return
        for root, dirs, files in os.walk(base):
            if stop_flag.is_set():
                return
            # 过滤系统/无关目录
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('$')]
            for f in files:
                if f.lower() == EXE_NAME.lower():
                    full = os.path.join(root, f)
                    if full.lower() not in seen and is_pcl_exe(full):
                        seen.add(full.lower())
                        found.append(full)
                        callback(full)
                if f == 'Plain Craft Launcher 2.sln':
                    src_dir = os.path.join(root, '..') if root.endswith('Plain Craft Launcher 2') else root
                    if src_dir.lower() not in seen:
                        seen.add(src_dir.lower())
                        found.append(src_dir + ' (源码)')
                        callback(src_dir + ' (源码)')
            callback(('progress', label + ': ' + root))

    # 第一轮：常见目录（快速）
    priority = [
        os.path.join(drive, 'Minecraft'),
        os.path.join(drive, 'PCL'),
        os.path.join(drive, 'PCL2'),
        os.path.join(drive, 'Games'),
        os.path.join(drive, 'Downloads'),
        os.path.join(drive, 'Desktop'),
        os.path.join(drive, 'Users'),
        os.path.join(drive, 'Program Files'),
        os.path.join(drive, 'Program Files (x86)'),
    ]
    for p in priority:
        if stop_flag.is_set(): return found
        if found: break  # 常见目录找到就不再继续
        scan_path(p, drive)

    # 第二轮：没找到则全盘根目录扫描
    if not found and not stop_flag.is_set():
        scan_path(drive, drive + ' (全盘)')

    return found


# ============================================================
# 持久化守护
# ============================================================

Q4 = chr(34) * 4  # VBS 中的四个引号
GUARD_VBS = (
    "' PCL2 下载补丁守护 - 自动在 PCL 更新后重新补丁\n"
    "Option Explicit\n"
    "Dim fso, shell, scriptDir, patcherExe\n"
    "Set fso = CreateObject(" + chr(34) + "Scripting.FileSystemObject" + chr(34) + ")\n"
    "Set shell = CreateObject(" + chr(34) + "WScript.Shell" + chr(34) + ")\n"
    "scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)\n"
    "patcherExe = fso.BuildPath(scriptDir, " + chr(34) + "PCL_PatchGuard.exe" + chr(34) + ")\n"
    "If fso.FileExists(patcherExe) Then\n"
    "    shell.Run " + Q4 + " & patcherExe & " + chr(34) + " --silent" + chr(34) + ", 0, False\n"
    "End If\n"
)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "PCL2_PatchGuard"


def install_persistence(logger, exe_dir):
    """安装持久化守护：复制自身 + 注册表开机启动"""
    try:
        import winreg
        current_exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])

        # 守护 exe 路径
        guard_exe = os.path.join(exe_dir, "PCL_PatchGuard.exe")
        guard_vbs = os.path.join(exe_dir, "PCL_PatchGuard.vbs")

        # 复制自身
        if getattr(sys, 'frozen', False):
            # 打包后的 exe
            if os.path.abspath(current_exe) != os.path.abspath(guard_exe):
                shutil.copy2(current_exe, guard_exe)
        else:
            # 脚本模式，写一个 vbs 启动 python 脚本
            pass

        # 写 VBS 守护脚本
        if getattr(sys, 'frozen', False):
            with open(guard_vbs, 'w', encoding='gbk') as f:
                f.write(GUARD_VBS)
        else:
            # Python 脚本模式
            q = chr(34)
            script_path = os.path.abspath(sys.argv[0]).replace('\\', '\\\\')
            vbs_content = (
                "' PCL2 下载补丁守护\n"
                "Option Explicit\n"
                "Dim shell\n"
                f"Set shell = CreateObject({q}WScript.Shell{q})\n"
                f"shell.Run {q}pythonw.exe {q}{q}{script_path}{q}{q} --silent{q}, 0, False\n"
            )
            with open(guard_vbs, 'w', encoding='gbk') as f:
                f.write(vbs_content)

        # 写注册表
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, f'"{guard_vbs}"')
        winreg.CloseKey(key)
        return True, f"守护已安装到: {exe_dir}"
    except Exception as e:
        return False, str(e)


def uninstall_persistence():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# ============================================================
# 静默模式（被守护脚本调用）
# ============================================================

class _SilentStop:
    def is_set(self): return False


def silent_run():
    """静默模式：搜索所有磁盘，找到 PCL 就补丁，然后退出"""
    log_lines = []
    def log(msg):
        log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    log("静默补丁开始")
    stop = _SilentStop()
    targets = []
    for drive in get_drives():
        def cb(x):
            if not isinstance(x, tuple) and x not in targets:
                targets.append(x)
        search_drive(drive, cb, stop)

    patched = 0
    for target in targets:
        path = target.replace(' (源码)', '') if target.endswith(' (源码)') else target
        if target.endswith(' (源码)'):
            ok, msg = patch_source(path, log)
        else:
            ok, msg = patch_exe(path, log)
        if ok:
            patched += 1
    log(f"完成，处理 {patched} 个目标")

    # 写日志（守护 exe 同目录）
    try:
        base = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0]))
        log_path = os.path.join(base, 'pcl_patch.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(log_lines) + '\n')
    except Exception:
        pass
    return patched


# ============================================================
# GUI
# ============================================================

class PatcherGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PCL2 下载功能恢复补丁工具")
        self.root.geometry("680x560")
        self.root.resizable(False, False)
        self.stop_search = threading.Event()

        self._build_ui()

    def _build_ui(self):
        pad = {'padx': 10, 'pady': 5}

        # 标题
        title = tk.Label(self.root, text="PCL2 下载功能恢复补丁",
                        font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(pady=(15, 5))
        sub = tk.Label(self.root, text="支持 2.99+ 版本恢复游戏本体下载 ｜ 持久化守护自动重打",
                      font=("Microsoft YaHei UI", 9), fg="#666")
        sub.pack()

        # 路径输入区
        frame = tk.LabelFrame(self.root, text="PCL 文件夹", font=("Microsoft YaHei UI", 10))
        frame.pack(fill='x', padx=15, pady=10)

        self.path_var = tk.StringVar()
        entry = tk.Entry(frame, textvariable=self.path_var, font=("Consolas", 10))
        entry.pack(side='left', fill='x', expand=True, padx=(8, 5), pady=8)

        btn_browse = tk.Button(frame, text="浏览...", width=8, command=self.on_browse)
        btn_browse.pack(side='left', padx=2)

        btn_search = tk.Button(frame, text="选择磁盘自动搜索", width=16, command=self.on_search_disk)
        btn_search.pack(side='left', padx=(2, 8))

        # 进度条
        prog_frame = tk.Frame(self.root)
        prog_frame.pack(fill='x', padx=15)
        self.progress = ttk.Progressbar(prog_frame, mode='determinate', maximum=100)
        self.progress.pack(side='left', fill='x', expand=True)
        self.prog_label = tk.Label(prog_frame, text="就绪", width=12, anchor='w',
                                   font=("Microsoft YaHei UI", 9))
        self.prog_label.pack(side='left', padx=8)

        # 操作按钮
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill='x', padx=15, pady=8)
        self.btn_patch = tk.Button(btn_frame, text="① 打补丁并安装守护", width=18, height=2,
                                   font=("Microsoft YaHei UI", 11, "bold"),
                                   bg='#2D8CF0', fg='white', command=self.on_patch)
        self.btn_patch.pack(side='left', padx=4)
        self.btn_persist = tk.Button(btn_frame, text="② 仅重装守护", width=14, height=2,
                                     font=("Microsoft YaHei UI", 10),
                                     command=self.on_install_persistence)
        self.btn_persist.pack(side='left', padx=4)
        self.btn_stop = tk.Button(btn_frame, text="停止搜索", width=10, height=2,
                                  state='disabled', command=self.on_stop)
        self.btn_stop.pack(side='right', padx=4)

        # 日志区
        log_frame = tk.LabelFrame(self.root, text="日志", font=("Microsoft YaHei UI", 10))
        log_frame.pack(fill='both', expand=True, padx=15, pady=(5, 12))

        self.log_text = tk.Text(log_frame, font=("Consolas", 9), wrap='word',
                                bg='#1e1e1e', fg='#d4d4d4', insertbackground='#d4d4d4',
                                relief='flat', state='disabled')
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log_text.pack(fill='both', expand=True, padx=5, pady=5)

        # 磁盘选择对话框相关
        self.search_results = []

    def log(self, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        def _append():
            self.log_text.configure(state='normal')
            self.log_text.insert('end', f"[{ts}] {msg}\n")
            self.log_text.see('end')
            self.log_text.configure(state='disabled')
        self.root.after(0, _append)

    def set_progress(self, value, text=None):
        def _update():
            self.progress['value'] = value
            if text:
                self.prog_label.config(text=text)
        self.root.after(0, _update)

    def on_browse(self):
        path = filedialog.askdirectory(title="选择 PCL 文件夹")
        if path:
            self.path_var.set(path)

    def on_search_disk(self):
        # 选择磁盘
        win = tk.Toplevel(self.root)
        win.title("选择磁盘")
        win.geometry("300x360")
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="选择要搜索的磁盘：", font=("Microsoft YaHei UI", 11)).pack(pady=10)
        listbox = tk.Listbox(win, selectmode='multiple', font=("Consolas", 11))
        for d in get_drives():
            listbox.insert('end', f"  {d}")
        listbox.pack(fill='both', expand=True, padx=15)
        listbox.selection_set(0, 'end')

        def start():
            sel = listbox.curselection()
            drives = [get_drives()[i] for i in sel]
            win.destroy()
            self._start_search(drives)

        tk.Button(win, text="开始搜索", width=15, height=1, command=start,
                  bg='#2D8CF0', fg='white').pack(pady=10)

    def _start_search(self, drives):
        self.stop_search.clear()
        self.btn_stop.config(state='normal')
        self.btn_patch.config(state='disabled')
        self.search_results = []
        self.progress['value'] = 0
        self.log(f"开始在 {len(drives)} 个磁盘搜索 PCL...")

        def worker():
            total = len(drives)
            for i, drive in enumerate(drives):
                if self.stop_search.is_set():
                    break
                self.log(f"扫描 {drive} ...")
                def cb(x):
                    if isinstance(x, tuple) and x[0] == 'progress':
                        self.set_progress((i / total) * 100 + (1/total)*30, f"扫描 {drive}")
                    else:
                        self.search_results.append(x)
                        self.log(f"  → 发现: {x}")
                search_drive(drive, cb, self.stop_search)
                self.set_progress(((i+1) / total) * 100, f"{drive} 完成")

            self.root.after(0, self._search_done)

        threading.Thread(target=worker, daemon=True).start()

    def _search_done(self):
        self.btn_stop.config(state='disabled')
        self.btn_patch.config(state='normal')
        self.set_progress(100, "搜索完成")
        if self.search_results:
            # 自动填入第一个
            first = self.search_results[0]
            if first.endswith(' (源码)'):
                first = first[:-5]
            self.path_var.set(os.path.dirname(first) if first.endswith('.exe') else first)
            self.log(f"搜索完成，共发现 {len(self.search_results)} 个 PCL，已自动填入第一个")
            # 多个结果时显示
            if len(self.search_results) > 1:
                for r in self.search_results[1:]:
                    self.log(f"  其他: {r}")
        else:
            self.log("未找到 PCL，请手动指定路径")

    def on_stop(self):
        self.stop_search.set()
        self.log("正在停止...")

    def on_patch(self):
        folder = self.path_var.get().strip()
        if not folder or not os.path.exists(folder):
            messagebox.showwarning("提示", "请先选择或输入有效的 PCL 文件夹路径（或点击自动搜索）")
            return
        self.btn_patch.config(state='disabled')
        self.btn_persist.config(state='disabled')
        self.progress['value'] = 0

        def worker():
            self.log("=" * 50)
            self.log(f"目标: {folder}")
            self.set_progress(15, "打补丁...")

            # 第一步：打补丁
            ok, msg = patch_pcl_folder(folder, self.log)
            if not ok:
                self.set_progress(0, "补丁失败")
                self.log(f"✘ 补丁失败: {msg}")
                self.root.after(0, lambda: messagebox.showerror("失败", f"补丁失败:\n{msg}"))
                self.btn_patch.config(state='normal')
                self.btn_persist.config(state='normal')
                return

            self.set_progress(70, "安装持久化守护...")

            # 第二步：自动安装持久化守护
            # 确定 PCL 目录（exe 所在目录）
            pcl_dir = folder
            if os.path.isfile(pcl_dir):
                pcl_dir = os.path.dirname(pcl_dir)
            else:
                exe_path = find_pcl_exe_in_folder(pcl_dir)
                if exe_path:
                    pcl_dir = os.path.dirname(exe_path)

            self.log("")
            self.log("正在安装持久化守护...")
            ok2, msg2 = install_persistence(self.log, pcl_dir)
            if ok2:
                self.log(f"✔ 持久化守护已安装: {msg2}")
                self.log("  以后每次开机登录会自动检查，PCL 更新后自动重新补丁")
                self.set_progress(100, "全部完成")
                self.root.after(0, lambda: messagebox.showinfo(
                    "完成",
                    f"补丁成功！\n{msg}\n\n持久化守护已安装到：\n{pcl_dir}\n\n"
                    "以后每次开机登录会自动检查，PCL 更新后下载功能会自动恢复。\n"
                    "原文件已备份为 .bak。"))
            else:
                self.set_progress(85, "补丁成功但守护失败")
                self.log(f"⚠ 补丁成功，但守护安装失败: {msg2}")
                self.root.after(0, lambda: messagebox.showwarning(
                    "部分完成", f"补丁成功！\n{msg}\n\n但持久化守护安装失败:\n{msg2}"))
            self.btn_patch.config(state='normal')
            self.btn_persist.config(state='normal')

        threading.Thread(target=worker, daemon=True).start()

    def on_install_persistence(self):
        folder = self.path_var.get().strip()
        if not folder:
            messagebox.showwarning("提示", "请先指定 PCL 文件夹")
            return
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        # 递归找 PCL 目录
        if not find_pcl_exe_in_folder(folder):
            if not is_source_folder(folder):
                if not messagebox.askyesno("确认", "未在该目录找到 PCL，仍要安装守护吗？"):
                    return

        ok, msg = install_persistence(self.log, folder)
        if ok:
            self.log(f"✔ 持久化守护已安装: {msg}")
            self.log("  以后每次开机登录都会自动检查并重新补丁")
            self.log("  PCL 更新后会自动恢复下载功能")
            messagebox.showinfo("成功", "持久化守护已安装！\n\n每次开机登录时自动检查并补丁，\nPCL 更新后下载功能会自动恢复。")
        else:
            messagebox.showerror("失败", msg)

    def run(self):
        self.root.mainloop()


# ============================================================
# 入口
# ============================================================

if __name__ == '__main__':
    if '--silent' in sys.argv:
        silent_run()
    else:
        app = PatcherGUI()
        app.run()
