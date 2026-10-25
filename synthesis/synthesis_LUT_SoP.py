#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import os
import re
import subprocess
from tempfile import NamedTemporaryFile

# ===============================
# BLIF parser
# ===============================

def parse_luts(lines):
    """
    Find .names blocks and return a list of (input names, output name, patterns with output '1', patterns with output '0') tuples.
    - Strips end-of-line comments (# ...)
    - Allows zero-length bit patterns (constant 1)
    """
    luts = []
    i = 0
    name_re  = re.compile(r'^\s*\.names\s+(.+)$')
    entry_re = re.compile(r'^([01-]*)\s*([01])(?:\s|$)')  # allow zero-length

    while i < len(lines):
        line = lines[i].strip()
        line = line.split('#', 1)[0].strip()
        m_name = name_re.match(line)
        if m_name:
            tokens = line.split()
            if len(tokens) < 2:
                i += 1
                continue
            inputs  = tokens[1:-1]
            output  = tokens[-1]
            i += 1

            patterns1 = []
            patterns0 = []
            while i < len(lines):
                row = lines[i].strip()
                row = row.split('#', 1)[0].strip()
                if not row or row.startswith('.names'):
                    break
                m_entry = entry_re.match(row)
                if m_entry:
                    bitstr, val = m_entry.group(1), m_entry.group(2)
                    if val == '1':
                        patterns1.append(bitstr)
                    else:
                        patterns0.append(bitstr)
                i += 1

            luts.append((inputs, output, patterns1, patterns0))
        else:
            i += 1

    return luts


def make_expression(inputs, patterns1, patterns0):
    """
    inputs: ['A','B',…]
    patterns1: patterns with output=1 (e.g. ['01-','1-0',…]; '' (empty string) = constant 1)
    patterns0: patterns with output=0
    → returns an expression in SoP or negated-SoP (~SoP) form
    """
    if patterns1:
        for pat in patterns1:
            if pat == '' or all(bit == '-' for bit in pat):
                return "1'b1"

        terms = []
        for pat in patterns1:
            lits = []
            for bit, name in zip(pat, inputs):
                if bit == '1':
                    lits.append(name)
                elif bit == '0':
                    lits.append(f'~{name}')
            if lits:
                terms.append(' & '.join(lits))
        return ' | '.join(f'({t})' for t in terms) if terms else "1'b0"

    if patterns0:
        zero_terms = []
        for pat in patterns0:
            lits = []
            for bit, name in zip(pat, inputs):
                if bit == '1':
                    lits.append(name)
                elif bit == '0':
                    lits.append(f'~{name}')
            if lits:
                zero_terms.append(' & '.join(lits))
        if zero_terms:
            inner = ' | '.join(f'({t})' for t in zero_terms)
            return f'~({inner})'

    return "1'b0"


def parse_port_list(lines, keyword):
    """
    Return the port list of an '.inputs' or '.outputs' block, handling '\' line continuations
    - Strips end-of-line comments
    """
    ports = []
    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip('\n')
        if line.startswith(keyword):
            rest = line[len(keyword):].rstrip()
            rest = rest.split('#', 1)[0].strip()

            cont = rest.endswith('\\')
            if cont:
                rest = rest[:-1]
            tokens = rest.split()

            while cont and i + 1 < len(lines):
                i += 1
                next_line = lines[i].rstrip('\n')
                part = next_line.split('#', 1)[0].strip()
                cont = part.endswith('\\')
                part = part.rstrip('\\').strip()
                if part:
                    tokens += part.split()

            ports = [tok for tok in tokens if tok and tok != '\\']
            break
    return ports


# ===============================
# Name cleanup: strip new_ + remove special characters + I*/O*
# ===============================

def strip_new(name, remove_all=True):
    return name.replace('new_', '') if remove_all else (name[4:] if name.startswith('new_') else name)

def sanitize_ident(name: str) -> str:
    """Remove [, ], \ from a name"""
    return re.sub(r'[\\\[\]]', '', name)

def core_name_of(n, inputs_orig, outputs_orig):
    """
    Naming rule used inside the core:
      - top-level input  -> 'I' + strip_new(n)
      - top-level output -> 'O' + strip_new(n)
      - internal net     -> strip_new(n)
      - finally remove [,],\
    """
    base = strip_new(n, remove_all=True)
    if n in inputs_orig:
        res = 'I' + base
    elif n in outputs_orig:
        res = 'O' + base
    else:
        res = base
    return sanitize_ident(res)


# ===============================
# Expression toggle utility (for ver2)
# ===============================

def toggle_neg(expr: str) -> str:
    """
    Toggle the top-level logical polarity of expr.
      - Invert the constants "1'b0"/"1'b1"
      - If it starts with "~(" and ends with ")", strip the outer ~(...)
      - Otherwise wrap the whole expression in ~(...)
    """
    s = expr.strip()
    if s in ("1'b0", "1'b1"):
        return "1'b1" if s == "1'b0" else "1'b0"
    if s.startswith('~(') and s.endswith(')'):
        return s[2:-1].strip()
    return f"~({s})"


# ===============================
# Verilog generation (standard version)
# ===============================

def render_core_verilog(core_name, inputs_ren, outputs_ren, wires, luts_ren):
    ports = inputs_ren + outputs_ren
    lines = []
    lines.append(f"module {core_name}({','.join(ports)});")
    if inputs_ren:
        lines.append("  input  " + ",".join(inputs_ren) + ";")
    if outputs_ren:
        lines.append("  output " + ",".join(outputs_ren) + ";")
    if wires:
        lines.append("  wire   " + ",".join(wires) + ";\n")
    else:
        lines.append("")

    for ins, out, p1, p0 in luts_ren:
        expr = make_expression(ins, p1, p0)
        lines.append(f"  assign {out} = {expr};")

    lines.append("endmodule\n")
    return "\n".join(lines)


# ===============================
# Verilog generation (non-standard ver2: LHS written as ~out)
# ===============================

def render_core_verilog_ver2(core_name, inputs_ren, outputs_ren, wires, luts_ren):
    """
    Generate a non-standard form with ~ prepended to the LHS:
      assign ~out = toggle(expr);
    """
    ports = inputs_ren + outputs_ren
    lines = []
    lines.append(f"module {core_name}({','.join(ports)});")
    if inputs_ren:
        lines.append("  input  " + ",".join(inputs_ren) + ";")
    if outputs_ren:
        lines.append("  output " + ",".join(outputs_ren) + ";")
    if wires:
        lines.append("  wire   " + ",".join(wires) + ";\n")
    else:
        lines.append("")

    for ins, out, p1, p0 in luts_ren:
        expr = make_expression(ins, p1, p0)
        neg_expr = toggle_neg(expr)
        lines.append(f"  assign ~{out} = {expr};")  # ← non-standard LHS

    lines.append("endmodule\n")
    return "\n".join(lines)


def render_wrapper_top(top_name, core_name, inputs_orig, outputs_orig, in_map, out_map):
    ports = inputs_orig + outputs_orig
    lines = []
    lines.append(f"module {top_name}({','.join(ports)});")
    if inputs_orig:
        lines.append("  input  " + ",".join(inputs_orig) + ";")
    if outputs_orig:
        lines.append("  output " + ",".join(outputs_orig) + ";")

    conns = []
    conns += [f".{in_map[n]}({n})" for n in inputs_orig]
    conns += [f".{out_map[n]}({n})" for n in outputs_orig]

    lines.append(f"  {core_name} u_core(")
    lines.append("    " + ",\n    ".join(conns) + ");")
    lines.append("endmodule\n")
    return "\n".join(lines)


# ===============================
# Main flow
# ===============================

def _is_dir_like(p: str) -> bool:
    # Treat as a directory if it has no extension or ends with a slash
    return (p.endswith(os.sep) or p.endswith('/') or os.path.splitext(p)[1] == '')

def _ver2_path(path: str) -> str:
    base, ext = os.path.splitext(path)
    return f"{base}_named{ext}" if ext else f"{path}_named"

def main():
    # ---- Config ----
    config = configparser.ConfigParser()
    config.read('LUT_syn_config.cfg')

    input_path   = config.get('input_output', 'input_path')
    input_format = config.get('input_output', 'input_format')   # blif / verilog, etc.
    LUT_format   = config.get('input_output', 'LUT_format')     # blif / verilog, etc.
    abc_dir_path = config.get('abc', 'abc_dir_path')
    LUT_path     = config.get('input_output', 'LUT_path')       # ABC output (intermediate result) path
    output_path  = config.get('input_output', 'output_path')    # file or directory

    # --- If output_path is a directory, automatically build <path under benchmarks>/<stem>_mapped.v ---
    if _is_dir_like(output_path):
        in_norm = os.path.normpath(input_path)
        parts = in_norm.split(os.sep)
        try:
            idx = parts.index('benchmarks')
            rel_parts = parts[idx+1:] if idx + 1 < len(parts) else [os.path.basename(in_norm)]
        except ValueError:
            rel_parts = [os.path.basename(in_norm)]  # file name only if 'benchmarks' is absent
        stem, _ = os.path.splitext(rel_parts[-1])
        rel_dir = os.path.join(*rel_parts[:-1]) if len(rel_parts) > 1 else ''
        output_path = os.path.normpath(os.path.join(output_path, rel_dir, f"{stem}_mapped.v"))

    # Create the output directory
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    K = config.get('LUT_Mapping', 'K')

    abc_exe_path = os.path.join(abc_dir_path, "abc")
    abc_rc_path  = os.path.join(abc_dir_path, "abc.rc")

    # ---- Build the ABC script ----
    with open('abc_script_template.abc', 'r') as f:
        abc_script = f.read()

    # Substitute with double quotes in case paths contain spaces
    abc_script = abc_script.replace('abc_rc_path', f'"{abc_rc_path}"')
    abc_script = abc_script.replace('input.blif', f'"{input_path}"')
    abc_script = abc_script.replace('read_blif',  f'read_{input_format}')
    abc_script = abc_script.replace('if -K 4',     f'if -K {K}')
    abc_script = abc_script.replace('write_verilog', f'write_{LUT_format}')
    abc_script = abc_script.replace('output.v',   f'"{LUT_path}"')

    # ---- Run ABC (safe temp file) ----
    with NamedTemporaryFile(delete=False, suffix=".abc") as tf:
        abc_script_path = tf.name
        tf.write(abc_script.encode('utf-8'))

    try:
        subprocess.run([abc_exe_path, "-f", abc_script_path], check=True)
    finally:
        try:
            os.remove(abc_script_path)
        except FileNotFoundError:
            pass

    # ---- Read BLIF ----
    with open(LUT_path, 'r') as f:
        blif_lines = f.readlines()
    # join BLIF continuation lines (a trailing backslash continues the line)
    _joined, _buf = [], ''
    for _l in blif_lines:
        _s = _l.rstrip('\n')
        if _s.rstrip().endswith('\\'):
            _buf += _s.rstrip()[:-1] + ' '
            continue
        _joined.append(_buf + _s + '\n'); _buf = ''
    if _buf:
        _joined.append(_buf + '\n')
    blif_lines = _joined

    # Module name from .model
    module_name = None
    for line in blif_lines:
        if line.startswith('.model'):
            raw = line.split()[1]
            module_name = os.path.basename(raw)
            break
    if not module_name:
        module_name = "top"

    # Original port names
    inputs_orig  = parse_port_list(blif_lines, '.inputs ')
    outputs_orig = parse_port_list(blif_lines, '.outputs ')

    # Parse LUTs
    luts = parse_luts(blif_lines)

    # ---- Apply renaming rules (I*/O* + strip new_ + remove special characters) ----
    inputs_ren  = [core_name_of(n, inputs_orig, outputs_orig) for n in inputs_orig]
    outputs_ren = [core_name_of(n, inputs_orig, outputs_orig) for n in outputs_orig]

    in_map  = {n: core_name_of(n, inputs_orig, outputs_orig) for n in inputs_orig}
    out_map = {n: core_name_of(n, inputs_orig, outputs_orig) for n in outputs_orig}

    luts_ren = []
    for ins, out, p1, p0 in luts:
        ins2 = [core_name_of(n, inputs_orig, outputs_orig) for n in ins]
        out2 = core_name_of(out, inputs_orig, outputs_orig)
        luts_ren.append((ins2, out2, p1, p0))

    # wire: (all LUT outputs) - (top-level outputs)
    all_outs = [out for _, out, _, _ in luts_ren]
    wires = sorted(set(all_outs) - set(outputs_ren))

    # ---- Generate core/wrapper (standard Verilog) ----
    core_name = module_name + "__core"
    core_verilog = render_core_verilog(core_name, inputs_ren, outputs_ren, wires, luts_ren)

    # Save the final output (standard Verilog)
    with open(output_path, 'w') as f:
        f.write(core_verilog)

    # Temporary file for CEC: [core + wrapper (keeps the top name)]
    with NamedTemporaryFile(delete=False, suffix=".v") as tfv:
        cec_tmp_path = tfv.name
        tfv.write(core_verilog.encode('utf-8'))
        wrapper_v = render_wrapper_top(module_name, core_name, inputs_orig, outputs_orig, in_map, out_map)
        tfv.write(wrapper_v.encode('utf-8'))

    # ---- ABC CEC (original BLIF vs standard Verilog) ----
    print(f"Result verilog is generated: {output_path}")
    cec_cmd = f'cec -n "{input_path}" "{cec_tmp_path}"; quit'
    try:
        proc = subprocess.run(
            [abc_exe_path, "-c", cec_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        out = proc.stdout.strip()
        if "Networks are equivalent." in out:
            print("\n" + "="*50)
            print(" 🎉 Networks are equivalent.")
            print("="*50 + "\n")
        else:
            print("\n" + "!"*50)
            print(" ⚠️  ABC CEC result (not equivalent):")
            print(out)
            print("!"*50 + "\n")
    finally:
        try:
            os.remove(cec_tmp_path)
        except FileNotFoundError:
            pass

    # ---- ver2: additionally save the non-standard LHS (~out) version (after CEC) ----
    ver2_path = _ver2_path(output_path)
    core_verilog_ver2 = render_core_verilog_ver2(core_name, inputs_ren, outputs_ren, wires, luts_ren)
    with open(ver2_path, 'w') as f:
        f.write(core_verilog_ver2)
    print(f"Non-standard verilog (ver2) is generated: {ver2_path}")


if __name__ == "__main__":
    main()
