"""OURS_TCAD26 - parallelism-driven, area-aware technology mapping for memristive logic-in-memory.

I. Kim, M. Kim, and H. Park, "A Parallelism-Driven, Area-Aware Technology Mapping Framework for Memristive
Logic-in-Memory," IEEE Trans. Computer-Aided Design of Integrated Circuits and Systems (TCAD), 2026.

Maps a NOR-of-NOR (NoN) LUT netlist onto an RxC memristor crossbar (MAGIC NOR/NOT) level by level:
floorplanning by LUT size, ILP-based LUT placement (Gurobi), Hanan-grid fan-in routing, parallel HNOR/VNOR
execution and post-processing of intermediate results. The output is the cycle-by-cycle instruction stream.

  python3 mappers/OURS_TCAD26.py -i benchmarks/netlists/ISCAS85/c432.v -c 64x64
  python3 mappers/OURS_TCAD26.py -i ... -c ... --placement prev --routing astar     (ablation)
"""
import re
import sys
#from collections import OrdereDict
import os
import re
import time
import argparse
from collections import defaultdict
from collections import Counter
from pathlib import Path
import sys
from pathlib import Path as _Path
import copy
import sys
import traceback, json

global Attempt
global Cycle
global lut_max_V

# Get the current user's home directory automatically
#home_dir = os.path.expanduser("~")

# Path setup
#Path = os.path.join(home_dir, 'python', 'Python_LUT_Otc_2nd')
#Path = os.getcwd()
#Path2 = os.getcwd()

# ---- command line ----
import argparse as _argparse
import os as _os
_METHOD = "OURS_TCAD26"
_parser = _argparse.ArgumentParser(description="OURS_TCAD26: technology mapping of a NOR-of-NOR LUT netlist onto a memristor crossbar")
_parser.add_argument("-i", "--input", required=True, help="NoN LUT netlist (.v), e.g. benchmarks/netlists/ISCAS85/c432.v")
_parser.add_argument("-c", "--crossbar", required=True, help="crossbar size RxC, e.g. 64x64")
_parser.add_argument("-o", "--out", default=None, help="output directory (default: results/<bench>_<RxC>_<method>)")
_parser.add_argument("--placement", choices=["ilp", "prev"], default="ilp", help="LUT placement: ilp = proposed ILP placement (default), prev = placement of the previous work (ablation)")
_parser.add_argument("--routing", choices=["hanan", "astar"], default="hanan", help="fan-in routing: hanan = proposed Hanan-grid routing (default), astar = A* routing of the previous work (ablation)")
_parser.add_argument("--threads", type=int, default=4, help="Gurobi threads (default 4)")
_args = _parser.parse_args()
_cb_rows, _cb_cols = (int(x) for x in _args.crossbar.lower().split("x"))
_bench_name = _os.path.splitext(_os.path.basename(_args.input))[0].replace("_mapped_named", "")
_suffix = ("_place-prev" if _args.placement == "prev" else "") + ("_route-astar" if _args.routing == "astar" else "")
_RESULT_DIR = _os.path.abspath(_args.out or _os.path.join("results", f"{_bench_name}_{_cb_rows}x{_cb_cols}_{_METHOD}{_suffix}"))
_os.makedirs(_RESULT_DIR, exist_ok=True)
_args.routing = {"hanan": "ours", "astar": "iccd"}[_args.routing]  # internal names
_args.mipgap = 0.05     # MIP gap of the placement ILPs
_args.split = "budget"  # a level whose ILP is solved in parts gives all parts one shared time budget


def _report():
    """One summary line on the terminal when the run ends (the detailed log is in log.txt)."""
    import re as _re
    import sys as _sys
    try:
        _last = open(_os.path.join(_RESULT_DIR, "instructions.txt"), encoding="utf-8").read().rstrip().splitlines()[-1]
    except (OSError, IndexError):
        _last = ""
    if _last.startswith("Place Cycle"):
        _total = sum(int(v) for v in _re.findall(r"\d+", _last))
        print(f"[{_METHOD}] {_bench_name} {_cb_rows}x{_cb_cols}: {_total} cycles ({_last.strip()}) -> {_RESULT_DIR}", file=_sys.__stdout__)
    else:
        print(f"[{_METHOD}] {_bench_name} {_cb_rows}x{_cb_cols}: mapping failed (see {_RESULT_DIR}/log.txt)", file=_sys.__stdout__)


import atexit as _atexit
_atexit.register(_report)
# ---- end command line ----
Path = _RESULT_DIR
Path2 = _RESULT_DIR


Attempt = f"{_bench_name}_{_cb_rows}x{_cb_cols}_{_METHOD}"
Cycle = 1
VNOR_Cycle = 0
HNOR_Cycle = 0
A_star_Cycle = 0
Place_Cycle = 0
Copy_IR_Cycle = 0
Reset_Cycle = 0
place_info_dict = {}
primary_inputs = []
lut_max_V = 0

# Global: list of (row, col) positions where each input (net) is placed
input_positions = defaultdict(list)

import time

# Per-function runtime records
runtimes = {}
section_times = {}


import time
from functools import wraps

import sys
# Open the log file
log_file_path = _os.path.join(Path, "log.txt")
log = open(log_file_path, 'w', encoding='utf-8', buffering=1)  # line-buffered
sys.stdout = log
sys.stderr = log



def timer(func):
    name = func.__name__
    runtimes[name] = {"count": 0, "total_time": 0.0}

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start

        runtimes[name]["count"] += 1
        runtimes[name]["total_time"] += elapsed
        return result

    return wrapper

def log_section_time(section_name, start_time):
    elapsed = time.time() - start_time
    if section_name not in section_times:
        section_times[section_name] = []
    section_times[section_name].append(elapsed)

##################################################################
def _compute_seed_with_map_lut(current_level_luts, area_VH, rows, cols, blocked=None):
    """
    Build a warm-start seed crossbar using only this module's Crossbar.map_lut().
    - The real LUTs/crossbar are not touched (deepcopy + temporary crossbar).
    - LUTs that fail to place are skipped (the ILP fills them in).
    """
    seed_cb = Crossbar(rows, cols)
    # In a split sub-level, the blocks taken by earlier parts are marked occupied so that the
    # warm-start placement is feasible for this part (an infeasible seed is dropped and Gurobi may
    # then find no solution within the time limit).
    for _br, _bc, _bh, _bw in (blocked or ()):
        for _r in range(_br, min(_br + _bh, rows)):
            for _c in range(_bc, min(_bc + _bw, cols)):
                seed_cb.grid[_r][_c] = '__blocked___blocked'
    # (Optional) to suppress file logging / side effects during seed generation:
    # setattr(seed_cb, "quiet", True)  # if a quiet option is used below

    # Avoid modifying the originals
    luts_copy = [copy.deepcopy(lut) for lut in current_level_luts]

    # map_lut signature: map_lut(self, lut, luts, area_VH)
    for lut_dup in luts_copy:
        ok = seed_cb.map_lut(lut_dup, None, area_VH)
        if not ok:
            print(f"[warmstart] seed placement fail for {lut_dup.name} (continue)")
    return seed_cb

def _snap_to_candidate(r0, c0, w, h, area_VH, rows, cols):
    # Snap to the tile-aligned candidate grid (start and step of the V/H bands)
    row_off = sum(area_VH.get(f"V{i}", 0) for i in range(2, h))
    col_off = sum(area_VH.get(f"H{i}", 0) for i in range(2, w))
    if h > 0:
        k = max(0, round((r0 - row_off)/h))
        r0 = row_off + k*h
    if w > 0:
        k = max(0, round((c0 - col_off)/w))
        c0 = col_off + k*w
    # Clamp to the crossbar boundary
    r0 = max(0, min(r0, rows - h))
    c0 = max(0, min(c0, cols - w))
    return r0, c0

def _is_ir_cell(val: str) -> bool:
    if not val or '_' not in val: return False
    a, b = val.split('_', 1)
    return a.lstrip('~') == b.lstrip('~')

def _find_origin_from_ir(grid, lut):
    # If the IR cell "{X}_~X" is at (r_ir, c_ir), origin = (r_ir-(h-1), c_ir-(w-1))
    h, w = lut.size[1], lut.size[0]
    name = lut.name
    R, C = len(grid), len(grid[0])
    for i in range(R):
        for j in range(C):
            v = grid[i][j]
            if v and v.startswith(name + "_") and _is_ir_cell(v):
                return max(0, i-(h-1)), max(0, j-(w-1))
    return None, None

def _infer_term_row_from_grid(grid, lut, r0, c0, t):
    # Compute new_r from the actual row of any literal of term t
    h, w = lut.size[1], lut.size[0]
    name = lut.name
    R, C = len(grid), len(grid[0])
    for dc in range(w-1):
        i = r0 + t
        j = c0 + dc
        if 0 <= i < R and 0 <= j < C:
            v = grid[i][j]
            if v and v.startswith(name + "_"):
                return t  # already laid out on row t -> new_r = t
    return None

def _find_literal_pos(grid, lut, t, lit, r0, c0):
    # (new_r, new_c) of this literal = (i-r0, j-c0)
    h, w = lut.size[1], lut.size[0]
    name = lut.name
    root = lit.lstrip('~')
    R, C = len(grid), len(grid[0])
    for rr in range(h-1):
        i = r0 + rr
        for cc in range(w-1):
            j = c0 + cc
            if 0 <= i < R and 0 <= j < C:
                v = grid[i][j]
                if v and v.startswith(name + "_"):
                    lit_here = v.split("_", 1)[1]
                    if lit_here != '0' and lit_here.lstrip('~') == root:
                        return rr, cc
    return None
# --- ILP runtimes (solve time accumulated per ILP) ---
def _vs_band_bounds(h: int, area_VH: dict):
    """Return the [lo, hi] (inclusive) range of r0 where LUTs with VS=h can be placed."""
    band_lo = sum(area_VH[f"V{i}"] for i in range(2, h))                     # band start
    band_hi = band_lo + area_VH[f"V{h}"] - h                                  # maximum r0 (inclusive)
    return band_lo, band_hi

ilp_times = {
    "ILP1_map_lut": {"count": 0, "total_time": 0.0},
    "ILP2_row_group_refine": {"count": 0, "total_time": 0.0},
}

def record_ilp_time(key: str, start_time: float):
    elapsed = time.perf_counter() - start_time
    ilp_times[key]["count"] += 1
    ilp_times[key]["total_time"] += elapsed

def print_ilp_summary():
    print("\n[ ILP solve time summary ]")
    for key, data in ilp_times.items():
        cnt = data["count"]
        tot = data["total_time"]
        avg = (tot / cnt) if cnt else 0.0
        print(f"{key}: {cnt} calls | total {tot:.6f} s | avg {avg:.9f} s")
##################################################################


####### Initial parsing ######
def parse_verilog(verilog_str):         
    global Path, primary_inputs
    global lut_max_V
    assigns = re.findall(r'assign\s+~(.*?)\s+=\s+(.*?);', verilog_str)
    luts = {}
    print("Parsing result:", assigns)

    # Initialize primary_inputs
    input_lines = re.findall(r'^\s*input\s+(.*?);', verilog_str, re.MULTILINE)
    for line in input_lines:
        # Split by commas -> strip spaces and leftover semicolons
        names = [name.strip().rstrip(';') for name in line.split(',')]
        primary_inputs.extend([n for n in names if n])
    #
    #  Build the path
    file_path = os.path.join(Path, "instructions.txt")

    # Create the directory if it does not exist
    os.makedirs(Path, exist_ok=True)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            data = "Final output\n"
            f.write(data)
        print(f"File saved: {file_path}")

    except Exception as e:
        print(f"File save failed: {e}")


    for output, expr in assigns:
        fanin = set(re.findall(r'\b\w+\b', expr)) - {output} 
        if '&' not in expr:
            num_product_terms = 1
        else: num_product_terms = len(expr.split('|'))   
        
        faninlen = len(fanin)
        if output in luts:
            print(f"Error: Multiple assignments for {output}")
            return 
        luts[output] = LUT(name=output, fanin=fanin, num_product_terms=num_product_terms, size=[faninlen + 1, num_product_terms + 1], level=555, expr=expr, row=[99], col=[99], fanlevel = 0, term_products=None)
        if lut_max_V < num_product_terms + 1: 
            lut_max_V = num_product_terms + 1
    return luts


######level, fanlevel, size optimization#######
def update_lut_info(luts):              
    for lut_name, lut in luts.items():
        width = len(lut.fanin) + 1
        height = lut.num_product_terms + 1
        
    for lut_name, lut in luts.items():
        k = 0
        for idx, r in enumerate(luts[lut_name].fanin):
            if r[0] != 'I':
                k = 10
        if k == 0: 
            luts[lut_name].level = 1
        elif k == 10:
            luts[lut_name].level = 555
        else: luts[lut_name].level = 25

    # Exact ASAP levels: an iterative depth-first traversal sets each LUT's level to 1 + the largest
    # level of its LUT fan-ins (1 for a LUT fed only by primary inputs), for circuits of any depth.
    _asap = {}
    for _root in luts:
        _stack = [_root]
        while _stack:
            _x = _stack[-1]
            if _x in _asap:
                _stack.pop()
                continue
            _todo = [f for f in luts[_x].fanin if f in luts and f != _x and f not in _asap]
            if _todo:
                _stack.extend(_todo)
                continue
            _asap[_x] = 1 + max([_asap[f] for f in luts[_x].fanin if f in luts and f != _x] + [0])
            _stack.pop()
    for lut_name in luts:
        luts[lut_name].level = _asap[lut_name]


    for idx, lut in enumerate(luts):
        for chek_lut in luts:
            if lut in luts[chek_lut].fanin:                
                luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)


    for lut in luts:
        if luts[lut].size[1] > 2:
            terms_before = luts[lut].expr.replace('(', '').replace(')','')
            terms = []
            temp =[]
            terms = terms_before.split('|')
            for idx, r in enumerate(range(luts[lut].size[1])):
                terms_elements = terms[idx].split('&') if idx < len(terms) else []  
                temp.append(len(terms_elements))
            luts[lut].size[0] = max(temp) + 1

    return luts


def _outer_negated(expr):
    """True if expr is ~( ... ) with the parenthesis spanning the whole expression (an off-set
    LUT written by the synthesis script as ~(SOP of the off-set))."""
    e = expr.strip()
    if not e.startswith("~("):
        return False
    depth = 0
    for i, ch in enumerate(e[1:], start=1):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return i == len(e) - 1
    return False


def annotate_lut_terms(luts):
    """
    Add a 2-D term_products attribute to each LUT object based on its expr.
    - size = [w, h]
    - First h-1 rows: the literal list of each OR term, padded on the right with '0'
      * If every OR term is a single literal, they are merged into one row.
    - Last row: filled with '.', with '~{lut.name}' (IR) in the last cell
    term_products: 2-D list of variable height
    """
    for lut in luts.values():
        w, h = lut.size[0], lut.size[1]
        # Split into OR terms
        # For ~(SOP) the terms are those of the inner SOP; the outer '~' is not attached to the first literal
        # (e.g. ~((n46 & n48)) gives the term n46 & n48, not ~n46 & n48).
        _e = lut.expr.strip()[1:] if _outer_negated(lut.expr) else lut.expr
        prods = [p.strip() for p in _e.replace('(', '').replace(')', '').replace('~~', '~').split('|')]
        lits_per_prod = [[lit.strip() for lit in prod.split('&') if lit.strip()] for prod in prods]
        grid = []
        # Special case: if every OR term is a single literal, merge them into one row
        if len(lits_per_prod) > 1 and all(len(lits) == 1 for lits in lits_per_prod):
            merged_row = [lits[0] for lits in lits_per_prod]
            # Pad with '0' if needed
            merged_row += ['0'] * (w - len(merged_row))
            grid.append(merged_row)
        else:
            # General case: one row per OR term
            for lits in lits_per_prod:
                row = lits + ['0'] * (w - len(lits))
                grid.append(row)
        # IR row: fill with '.' and put '~lut.name' last
        ir_row = ['.'] * (w - 1) + [f"~{lut.name}"]
        grid.append(ir_row)
        lut.term_products = grid
    return luts


def Optimizing_luts(luts):   
    global lut_max_V       
    ordered_luts = sorted(luts.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
    max_level = ordered_luts[-1].level
    mapping_info = [[[0 for _ in range(4)]for _ in range(lut_max_V)]for _ in range(max_level)]  # 3-D list: one 4x4 list per level (max_level of them)

    for lut in ordered_luts:
        level = lut.level - 1
        row = lut.size[1] -2
        col = lut.size[0] -2
        mapping_info[level][row][col] += 1   #index note: level+1 = level, row+2 = vertical, col+2 = horizontal

    return mapping_info    


#Records the coordinates placed before A* routing
@timer
def place_information(r,c,signed_value,lut, pos_value):
    global place_info_dict
    if pos_value[0] == "~":
        pos_value = pos_value[1:]
    if 'I' not in signed_value:
        #if lut.fanlevel > lut.level:
            if pos_value not in place_info_dict:
                place_info_dict[pos_value] = []
            # A cell with literal L must hold NOT(L) for the HNOR to form the product term (the same
            # polarity flip applied to primary inputs, paper Sec. II-B rule 2). The routing target polarity
            # is that held value: 0 ('~') for a positive literal, 1 for a negated one.
            if pos_value == signed_value:
                polarity = 0
            else:
                polarity = 1
            
            new_item = ((r, c), polarity)
            if new_item not in place_info_dict[pos_value]:  # avoid duplicates
                place_info_dict[pos_value].append(new_item)


class LUT:
    def __init__(self, name, fanin, num_product_terms, size, level, expr, row, col, fanlevel, term_products):
        self.name = name
        self.fanin = fanin
        self.num_product_terms = num_product_terms
        self.size = size
        self.level = level
        self.expr = expr
        self.row = row
        self.col = col
        self.fanlevel = fanlevel
        self.term_products = term_products
        
    def __repr__(self): 
        return f"LUT(name={self.name}, fanin={self.fanin}, num_product_terms={self.num_product_terms}, size={self.size}, level={self.level}, expr={self.expr}, row={self.row}, col={self.col}, fanlevel={self.fanlevel}, term_products = {self.term_products})"

def initialize_crossbar(rows, cols):

    return Crossbar(rows, cols)




#######Crossbar size allocation, lut mapping#######
#######Crossbar size allocation, lut mapping#######
class Crossbar:
    def __init__(self, rows, cols):
        #self.grid = [[None for _ in range(cols)] for _ in range(rows)]
        self.rows = rows
        self.cols = cols
        self.grid = [[None for _ in range(cols)] for _ in range(rows)]


    def __str__(self):
        #return '\n'.join([' '.join(["%-8s" % cell if cell else '    .   ' for cell in row]) for row in self.grid])
        #return '\n'.join([' '.join([cell or '    .   ' for cell in row]) for row in self.grid])
        return '\n'.join([' '.join(["%-8s" % cell if cell else '    .   ' for cell in row]) for row in self.grid])
    def __repr__(self):
        return self.__str__() 
    
    #######Final lut mapping here#######


    # --- Block placement function ---
    '''def place_lut_block(self, lut, origin_r, origin_c):
        """
        (Important) assumes lut.size = [w, h].
        Checks the block boundary + sets the IR cell once + records lut.row/col + logs.
        """
        global Cycle, Attempt, Path, Place_Cycle

        try:        
            w, h = lut.size
            rows, cols = self.rows, self.cols

            # 1) Boundary check (prevents out-of-range)
            if origin_r < 0 or origin_c < 0 or origin_r + h > rows or origin_c + w > cols:
                raise ValueError(
                    f"place_lut_block: origin ({origin_r},{origin_c}) with size [w={w},h={h}] "
                    f"exceeds grid ({rows}x{cols})"
                )

            # 2) IR coordinates (bottom-right corner of the block)
            ir_r = origin_r + h - 1
            ir_c = origin_c + w - 1

            # 3) Prevent IR cell duplication/conflict
            if self.grid[ir_r][ir_c] is not None:
                raise RuntimeError(
                    f"place_lut_block: IR cell already occupied at ({ir_r},{ir_c}): {self.grid[ir_r][ir_c]}"
                )

            # 4) Record LUT metadata
            lut._alloc_origin = (origin_r, origin_c)
            lut.row = list(range(origin_r, origin_r + h))
            lut.col = list(range(origin_c, origin_c + w))

            # 5) Write the IR cell
            self.grid[ir_r][ir_c] = f"{lut.name}_~{lut.name}"

            # 6) Logging (keeps the usual format where possible)
            for r_off in range(h - 1):  # skip the IR row
                file_path = os.path.join(Path, "instructions.txt")
                with open(file_path, 'a', encoding='utf-8') as f:
                    data = (
                        f"C{Cycle} Place LUT{lut.name} "
                        f"({lut.row[0] + r_off}, {lut.col[0]}), "
                        f"({lut.row[0] + r_off}, {lut.col[-1]}) \n"
                    )
                    f.write(data)
                Cycle += 1
                Place_Cycle += 1
        except Exception as e:
            raise RuntimeError(
                f"[place_lut_block] lut={getattr(lut,'name',None)} origin=({origin_r},{origin_c}) size={getattr(lut,'size',None)}"
            ) from e'''
    def place_lut_block(self, lut, origin_r, origin_c):
        """
        Place LUT IR at last cell of block and initialize term_products ordering.
        """
        global Cycle
        global Attempt
        global Path
        global Place_Cycle

        w, h = lut.size
        # record origin
        lut._alloc_origin = (origin_r, origin_c)
        # IR at bottom-right
        ir_r = origin_r + h - 1
        ir_c = origin_c + w - 1
        # The label after '_' names the value the cell holds. The VNOR of the placed terms is
        # NOT(SOP): for a plain SOP LUT that is NOT X ('~X'); for a ~(SOP) LUT the terms are the inner SOP's,
        # so the cell holds X ('X').
        self.grid[ir_r][ir_c] = f"{lut.name}_{lut.name}" if _outer_negated(lut.expr) else f"{lut.name}_~{lut.name}"
        # Record the LUT coordinates
        lut.row = list(range(origin_r, origin_r + h))
        lut.col = list(range(origin_c, origin_c + w))
        for r in range(lut.size[1]-1): ####################
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a') as f:
                # The row's cell labels (term literals, '0' fillers, output column) are appended after '|'
                # so that the instruction stream can be replayed value by value
                data = f"C{Cycle} Place LUT{lut.name} ({lut.row[0] + r}, {lut.col[0]}), ({lut.row[0] + r}, {lut.col[-1]}) | {' '.join(lut.term_products[r])}\n"
                f.write(data)
                Cycle += 1
                Place_Cycle += 1

    # --- Internal literal placement function ---
    def place_term_instance(self, lut, term_idx, col_idx, net):
        global place_info_dict

        """
        Place a single literal instance for the reordered term at computed offsets.
        term_idx: row offset within block (0..h-2)
        col_idx: column offset within block (0..w-1)
        net: literal name, including '0'
        """
        r0, c0 = lut._alloc_origin
        r = r0 + term_idx
        c = c0 + col_idx
        self.grid[r][c] = f"{lut.name}_{net}"
        # record position if needed for future objectives (skip for placeholders)
        if net[0] == "~":
                pos_value = net[1:]
        else: pos_value = net
        
        if lut.name != pos_value:
            if net not in ('0',):
                input_positions[net].append((r, c))
                if net[0] == "~":
                    pos_value = net[1:]
                else: pos_value = net
                place_information(r,c,net,lut,pos_value)

    def map_lut(self, lut, luts, area_VH):
        global Cycle
        global Attempt
        global Path
        
        can_c, can_r = self.can_place(lut, area_VH)

        for i in can_c:
            for j in can_r:
                #can_c = self.can_place(i, j, lut, luts)
                if self.grid[j][i] is None:
                    this_c=can_c
                    for c in can_c:
                        if c < i:
                            this_c.remove(c)

                    temp = self.place_lut(can_r, this_c, lut)
                    if temp == False: return False
                    return True 
                else: False
                if not can_c and j == len(self.grid)-1 and i == len(self.grid[0])-1:
                    return False
        return False


    #######Check whether the LUT can be placed#######
    def can_place(self, lut, area_VH):
        global lut_max_V  
        size = lut.size
        can_c = []
        can_r =[] 
        temp_r=0
        temp_c=0
        for row in range(lut_max_V-1):
            if row+2 < int(size[1]):
                    temp_r+=int(area_VH["V"+str(row+2)])
            elif row+2 == int(size[1]):
                if int(area_VH["V"+str(row+2)]) == 0: ###############ROW 1
                    for n in range(temp_r):
                        can_r.append(temp_r+n)
                else:
                    for n in range(int(area_VH["V"+str(row+2)])):
                        can_r.append(temp_r+n)


        for col in range(4):
            if col+2 < int(size[0]):
                temp_c+=int(area_VH["H"+str(col+2)])
            elif col+2 == int(size[0]):
                if int(area_VH["H"+str(col+2)]) == 0:
                    for n in range(temp_c):
                        can_c.append(temp_c+n)
                else:
                    for n in range(int(area_VH["H"+str(col+2)])):
                        can_c.append(temp_c+n)

        return can_c, can_r
    ###############
    def optimize_can_c(self, can_r, this_c, lut):
        can_c=[]
        flag=0
        for c in this_c:
            flag=0
            for r in can_r:
                if all(self.grid[r][c+i] is None for i in range(lut.size[0])):
                    flag+=1
                    if flag == (int(lut.size[1])):
                        can_c=[c+n for n in range(int(lut.size[0]))]
                        this_r = r - (lut.size[1]-1)
                        return can_c, this_r 
        return None, None

    def place_lut(self, can_r, this_c, lut):
        import os
        global Cycle, Attempt, Path, Place_Cycle, lut_last_col

        # --- helper: literal normalization (handles the parity of leading ~) ---
        def normalize_lit(lit: str) -> str:
            s = lit.strip()
            # count leading tildes
            i = 0
            while i < len(s) and s[i] == "~":
                i += 1
            rest = s[i:].strip()
            if rest == "":
                return rest
            # Even number of ~ -> no negation; odd number -> a single ~
            return ("~" + rest) if (i % 2 == 1) else rest

        # Compute candidate columns/rows
        can_c, row = self.optimize_can_c(can_r, this_c, lut)
        if can_c is None:
            return False

        # Split expr into OR terms
        raw_expr = lut.expr.replace('(', '').replace(')', '')
        and_present = ('&' in lut.expr)
        if and_present:
            or_terms = [t.strip() for t in raw_expr.split('|')]
            # Sort by number of ANDs, descending
            or_terms.sort(key=lambda x: x.count('&'), reverse=True)
        else:
            # Pure OR (or a single literal): keep the string as is (only the first row is used for placement)
            or_terms = raw_expr  # string

        temp_row = []
        temp_col = []


        # Actual placement
        for idx, r_off in enumerate(range(lut.size[1])):
            # Build term_elements for this row
            if and_present:
                term_elements = or_terms[idx].split('&') if idx < len(or_terms) else []
            else:
                term_elements = or_terms.split('|') if idx < 1 else []

            # --- Normalization: remove double negations such as ~~ from literals ---
            term_elements = [normalize_lit(x) for x in term_elements if x.strip() != ""]

            if row + r_off not in temp_row:
                temp_row.append(row + r_off)

            for idx2, c in enumerate(can_c):
                if row + r_off >= len(self.grid) or c >= len(self.grid[0]):
                    return False

                # ---- Input placement: only at the first column ----
                if idx2 == 0 and term_elements:
                    # value is the list of normalized literals
                    value = [v for v in term_elements if v != ""]
                    loop = 0
                    # Safety limit against infinite loops (with some margin)
                    safety_limit = max(1, len(value) * 4 + 4)

                    while len(value) != 0 and loop < safety_limit:
                        placed_this_round = False
                        r_correct = []
                        c_correct = []
                        loop += 1

                        # Scan all of value for an item that can be placed first
                        for v_i, value2 in enumerate(list(value)):  # iterate over a copy
                            place = False
                            # value2 is already normalized, so pos_value follows from its first character
                            pos_value = value2[1:] if value2.startswith("~") else value2

                            for cc in can_c[:-1]:  # exclude the last column (output)
                                if place:
                                    break
                                if self.grid[row + r_off][cc] is not None:
                                    continue
                                for i in range(len(self.grid[0])):
                                    if place:
                                        break
                                    for rr in range(len(self.grid)):
                                        cell = self.grid[rr][i]
                                        if cell and "_" in cell:
                                            tok = cell.split("_", 1)[1]
                                            # Compare tok with pos_value (tok is "~X" or "X")
                                            if tok == pos_value or tok == "~" + pos_value:
                                                # Signal already in the same row (to the left) or in the same column
                                                if rr == row + r_off and i < cc:
                                                    r_correct.append((row + r_off, cc))
                                                elif i == cc:
                                                    c_correct.append((row + r_off, cc))

                            # Placement priority
                            target = None
                            if len(r_correct) == 0 and len(c_correct) != 0:
                                target = c_correct[0]
                            elif len(r_correct) != 0 and len(c_correct) == 0:
                                target = r_correct[0]
                            elif len(r_correct) != 0 and len(c_correct) != 0:
                                # Prefer the intersection (in effect the same as choosing c_correct[0])
                                pref = c_correct[0]
                                target = pref if pref in r_correct else pref

                            # fallback: if loop > len(term_elements), force placement into an empty cell
                            if target is None and loop > len(term_elements):
                                for ccc in can_c[:-1]:
                                    if self.grid[row + r_off][ccc] is None:
                                        target = (row + r_off, ccc)
                                        break

                            if target is not None:
                                tr, tc = target
                                # value2 is already normalized here too, so use it as is
                                self.grid[tr][tc] = lut.name + "_" + value2
                                pos_value = value2
                                if pos_value[0] == "~":
                                    pos_value = pos_value[1:]
                                #place_information(tr,tc,value2,lut,pos_value)
                                del value[v_i]
                                place = True
                                placed_this_round = True
                                break  # value loop

                        # Still not placed and at the fallback point: force value[0] into the first empty cell
                        if not placed_this_round and loop > len(term_elements):
                            fallback_target = None
                            for ccc in can_c[:-1]:
                                if self.grid[row + r_off][ccc] is None:
                                    fallback_target = (row + r_off, ccc)
                                    break
                            if fallback_target:
                                tr, tc = fallback_target
                                self.grid[tr][tc] = lut.name + "_" + value.pop(0)
                                pos_value = value.pop(0)
                                if pos_value[0] == "~":
                                    pos_value = pos_value[1:]
                                #place_information(tr,tc,value.pop(0),lut,pos_value)

                # ---- Fill the LUT cells (output/padding) ----
                if self.grid[row + r_off][c] is None:
                    if idx2 == lut.size[0] - 1:
                        if r_off == lut.size[1] - 1:
                            # Always output "~"
                            self.grid[row + r_off][c] = lut.name + "_~" + lut.name
                        else:
                            self.grid[row + r_off][c] = lut.name + "_0"
                    elif r_off == lut.size[1] - 1:
                        self.grid[row + r_off][c] = None
                    else:
                        self.grid[row + r_off][c] = lut.name + "_0"

        lut.row = temp_row
        lut.col = can_c

        return True    


#######Place LUTs + fanins#######
    # Size-based ILP split. A sub-level whose placement ILP is estimated above _ILP_VAR_LIMIT variables
    # is placed in two halves (recursively) in order; smaller ILPs are solved in one piece. This bounds
    # the model construction time on large crossbars (256x256 designs would otherwise build models of
    # ~450K variables and 1.4M constraints, taking tens of minutes per level in Python alone).
    _ILP_VAR_LIMIT = 150000

    def _ilp_size_estimate(self, luts, area_VH):
        total = 0
        for lut in luts:
            w, h = lut.size
            n_rows = area_VH[f"V{h}"] // h if area_VH.get(f"V{h}") else 0
            n_cols = area_VH[f"H{w}"] // w if area_VH.get(f"H{w}") else 0
            cands = n_rows * n_cols
            lits = sum(1 for row in lut.term_products[:-1] for v in row if v not in ('0', '.'))
            total += cands * (1 + (h - 1) * (h - 1) + lits * (w - 1))  # y has no row index
        return total

    def place_level_prev(self, luts, area_VH):
        """--placement prev (ablation): Prev.'s heuristic placement instead of the
        ILP. The origin is chosen as in Prev. (can_place + optimize_can_c: first column of the band region with h
        free rows) and the literals stay in expression order (term_products as annotated). Cells are written
        through the same calls as an ILP solution (place_lut_block + place_term_instance), so labels, fan-in
        registration, NOR schedule and routing are those of Ours."""
        for lut in luts:
            w, h = lut.size
            can_c, can_r = self.can_place(lut, area_VH)
            spot = None
            for c in can_c:
                free_rows = 0
                for r in can_r:
                    if all(0 <= c + k < self.cols and self.grid[r][c + k] is None for k in range(w)):
                        free_rows += 1
                        if free_rows == h:
                            r0 = r - (h - 1)
                            if r0 >= 0 and all(self.grid[r0 + dr][c + dc] is None for dr in range(h) for dc in range(w)):
                                spot = (r0, c)
                            break
                if spot:
                    break
            if spot is None:
                print(f"[prev-placement] no free block for {lut.name} {lut.size}")
                return False
            lut._alloc_origin = spot
            self.place_lut_block(lut, spot[0], spot[1])
            for t in range(h - 1):
                for c_idx, net in enumerate(lut.term_products[t]):
                    if net != '.':
                        self.place_term_instance(lut, t, c_idx, net)
        return True

    def _ilp_leaves(self, luts, area_VH):
        """Number of ILP parts the size-based split makes of this LUT list (same halving rule)."""
        if len(luts) > 1 and self._ilp_size_estimate(luts, area_VH) > self._ILP_VAR_LIMIT:
            half = len(luts) // 2
            return self._ilp_leaves(luts[:half], area_VH) + self._ilp_leaves(luts[half:], area_VH)
        return 1

    _LEVEL_BUDGET = 120.0   # ILP time limit per level; a level solved in parts shares it among the parts
    _PART_MIN_TL = 1.0      # ...but every part gets at least this much (its warm start is a feasible solution)

    def map_lut_batched(self, luts, area_VH, _blocked=None):
        if _args.placement == "prev":  # placement ablation
            return self.place_level_prev(luts, area_VH)
        if _blocked is None:  # top-level call for this level
            self._budget = None
            if _args.split == "budget" and len(luts) > 1 and self._ilp_size_estimate(luts, area_VH) > self._ILP_VAR_LIMIT:
                self._budget = {"deadline": time.time() + self._LEVEL_BUDGET, "left": self._ilp_leaves(luts, area_VH)}
                print(f"[ILP split] level split into {self._budget['left']} ILP parts sharing a {self._LEVEL_BUDGET:.0f} s budget")
        if _args.split != "none" and len(luts) > 1 and self._ilp_size_estimate(luts, area_VH) > self._ILP_VAR_LIMIT:
            half = len(luts) // 2
            print(f"[ILP split] ILP estimate above {self._ILP_VAR_LIMIT}: placing {len(luts)} LUTs as {half} + {len(luts) - half}")
            blocked = list(_blocked or [])
            outer = getattr(self, "_no_refine", False)
            self._no_refine = True
            try:
                if not self.map_lut_batched(luts[:half], area_VH, blocked):
                    return False
                blocked += [(l._alloc_origin[0], l._alloc_origin[1], l.size[1], l.size[0]) for l in luts[:half]]
                return self.map_lut_batched(luts[half:], area_VH, blocked)
            finally:
                self._no_refine = outer
        return self.map_lut_with_reset(luts, area_VH, blocked=_blocked)

    @timer
    def map_lut_with_reset(self, current_level_luts, area_VH, warm_seed=None, blocked=None):
        from gurobipy import Model, GRB, quicksum
        from itertools import combinations

        m = Model('map_lut_ilp')
        m.setParam('Threads', _args.threads)
        # Time limit (seconds)
        m.setParam('TimeLimit', 120)
        m.setParam('OutputFlag', 1)
        if _args.mipgap is not None:  # MIP gap (0.05 by default)
            m.setParam('MIPGap', _args.mipgap)
        # (Optional) to save the log to a file:
        # m.setParam('LogFile', 'map_lut_ilp.log')

        # === 1) Compute candidate origins (tile-aligned by size) ===
        candidate = {}
        for lut in current_level_luts:
            w, h = lut.size
            # row range
            row_offset = sum(area_VH[f"V{i}"] for i in range(2, h))
            num_row_tiles = area_VH[f"V{h}"] // h
            r0s = [row_offset + k * h for k in range(num_row_tiles)]
            # column range
            col_offset = sum(area_VH[f"H{i}"] for i in range(2, w))
            num_col_tiles = area_VH[f"H{w}"] // w
            c0s = [col_offset + k * w for k in range(num_col_tiles)]
            # valid origins
            candidate[lut.name] = [
                (r0, c0) for r0 in r0s for c0 in c0s
                if r0 + h <= self.rows and c0 + w <= self.cols
                # When a sub-level is placed in several ILP parts, a later part skips the blocks taken
                # by the earlier parts
                and not any(r0 < br + bh and br < r0 + h and c0 < bc + bw and bc < c0 + w for br, bc, bh, bw in (blocked or ()))
            ]

        # === 2) Variable definitions ===
        # origin selection
        x = m.addVars(
            [(lut.name, r0, c0)
             for lut in current_level_luts
             for (r0, c0) in candidate[lut.name]],
            vtype=GRB.BINARY, name='x'
        )
        # lit_names: primary inputs excluded, sign (~) stripped so that the same literal is merged
        lit_names = {
            lit_stripped
            for lut in current_level_luts
            for t in lut.term_products[:-1]
            for lit in t
            if lit not in ('0', '.')
            for lit_stripped in [lit.lstrip('~')]
            if lit_stripped not in primary_inputs
        }
        # Placement ILP variables: x (LUT origin), z (row of each product term), y (column of each net in its
        # term; its row is the term row), R/C (rows/columns used by each net, on reachable rows/columns only).
        # Precompute the reachable rows/columns of each literal (limits the ranges of R and C)
        reachable_rows = {lit: set() for lit in lit_names}
        reachable_cols = {lit: set() for lit in lit_names}
        for lut in current_level_luts:
            w, h = lut.size
            for t_idx, lits in enumerate(lut.term_products[:-1]):
                for lit in lits:
                    if lit == '0' or lit.lstrip('~') in primary_inputs:
                        continue
                    root = lit.lstrip('~')
                    if root in lit_names:
                        for (r0, c0) in candidate[lut.name]:
                            for nr in range(h - 1):
                                reachable_rows[root].add(r0 + nr)
                            for nc in range(w - 1):
                                reachable_cols[root].add(c0 + nc)
        # Row order of each term
        z = m.addVars(
            [(lut.name, t, new_r, r0, c0)
             for lut in current_level_luts
             for t in range(lut.size[1] - 1)
             for new_r in range(lut.size[1] - 1)
             for (r0, c0) in candidate[lut.name]],
            vtype=GRB.BINARY, name='z'
        )
        # Column order of each literal (no new_r dimension: z determines the row)
        y = m.addVars(
            [
             (lut.name, t, lit, new_c, r0, c0)
             for lut in current_level_luts
             for t, lits in enumerate(lut.term_products[:-1])
             for lit in lits
             if lit != '0' and lit.lstrip('~') not in primary_inputs
             for new_c in range(lut.size[0] - 1)
             for (r0, c0) in candidate[lut.name]
            ],
            vtype=GRB.BINARY, name='y'
        )
        # === 3) Additional variables ===
        # 3.1) global row-placement indicator S^R[l,t,k,i]
        # lit = literal name
        # lit_names: set of distinct literal names used in the whole verilog netlist
        # R and C are limited to reachable rows/columns
        # 3.1) global row-usage indicator R[lit, i] (reachable rows only)
        R = m.addVars(
            [(lit, i) for lit in lit_names for i in reachable_rows[lit]],
            vtype=GRB.BINARY, name="R"
        )
        # 3.2) global col-usage indicator C[lit, j] (reachable cols only)
        C = m.addVars(
            [(lit, j)
            for lit in lit_names
            for j in reachable_cols[lit]],
            vtype=GRB.BINARY,
            name="C"
        )

        # === Block non-overlap constraints (sparse: only cells covered by two or more LUT candidates) ===
        from collections import defaultdict as _dd_overlap
        _cell_covers = _dd_overlap(list)
        for lut in current_level_luts:
            w, h = lut.size
            for (r0, c0) in candidate[lut.name]:
                for di in range(h):
                    for dj in range(w):
                        _cell_covers[(r0 + di, c0 + dj)].append((lut.name, r0, c0))
        for (i, j), covers in _cell_covers.items():
            if len(covers) >= 2:
                m.addConstr(
                    quicksum(x[name, r0, c0] for name, r0, c0 in covers) <= 1
                )

        # === 3) Constraints ===
        for lut in current_level_luts:
            w, h = lut.size
            # (1) exactly one origin
            m.addConstr(
                quicksum(x[lut.name, r0, c0]
                         for (r0, c0) in candidate[lut.name]) == 1
            )
            # (2) row/column order of each term
            for t, lits in enumerate(lut.term_products[:-1]):
                #nonzeros = [lit for lit in lits if lit!='0']
                # literals included in the ILP (primary inputs excluded)
                nonzeros = [
                    lit
                    for lit in lits
                    if lit != '0'
                    and lit.lstrip('~') not in primary_inputs
                ]
                # row selection
                m.addConstr(
                    quicksum(z[lut.name, t, new_r, r0, c0]
                             for new_r in range(h - 1)
                             for (r0, c0) in candidate[lut.name]) == 1
                )
                for new_r in range(h - 1):
                    for (r0, c0) in candidate[lut.name]:
                        m.addConstr(
                            z[lut.name, t, new_r, r0, c0]
                            <= x[lut.name, r0, c0]
                        )
                # column selection (no new_r)
                for lit in nonzeros:
                    # (a) each literal lit must be placed exactly once
                    m.addConstr(
                        quicksum(
                            y[lut.name, t, lit, new_c, r0, c0]
                            for new_c in range(w - 1)
                            for (r0, c0) in candidate[lut.name]
                        ) == 1
                    )

                    # y <= x (instead of y <= z; z determines the row separately)
                    for new_c in range(w - 1):
                        for (r0, c0) in candidate[lut.name]:
                            m.addConstr(
                                y[lut.name, t, lit, new_c, r0, c0]
                                <= x[lut.name, r0, c0]
                            )

            # (2.5) per-term column uniqueness (no two literals of one term in the same column)
            for (r0, c0) in candidate[lut.name]:
                for t_idx in range(h - 1):
                    nonzeros_t = [
                        lit for lit in lut.term_products[t_idx]
                        if lit != '0' and lit.lstrip('~') not in primary_inputs
                    ]
                    if len(nonzeros_t) > 1:
                        for new_c in range(w - 1):
                            m.addConstr(
                                quicksum(
                                    y[lut.name, t_idx, lit, new_c, r0, c0]
                                    for lit in nonzeros_t
                                ) <= 1
                            )
            # (2.6) row uniqueness: at most one term per row for a given origin
            for (r0, c0) in candidate[lut.name]:
                for new_r in range(h - 1):
                    m.addConstr(
                        quicksum(
                            z[lut.name, t_idx, new_r, r0, c0]
                            for t_idx in range(h - 1)
                        ) <= 1
                    )
        

        # y -> R/C link (no new_r; R is linked to z)
        # LUT name -> object map (avoids repeated lookups)
        _lut_by_name = {l.name: l for l in current_level_luts}
        # C link: the column is determined directly by y
        for (lut_name, t, lit, new_c, r0, c0), yvar in y.items():
            root = lit.lstrip('~')
            j = c0 + new_c
            m.addConstr(C[root, j] >= yvar)
        # R link: the row is determined by z -> R[root, r0+new_r] >= z (2 nonzeros)
        for (lut_name, t, new_r, r0, c0), zvar in z.items():
            i = r0 + new_r
            lut_obj = _lut_by_name[lut_name]
            for lit in lut_obj.term_products[t]:
                if lit not in ('0', '.'):
                    root = lit.lstrip('~')
                    if root not in primary_inputs and (root, i) in R:
                        m.addConstr(R[root, i] >= zvar)

        ########################################################################
        ######### Alternative R/C link constraints (disabled) ########
        # z -> R link (the sign is stripped for the term row selection too)
        '''for (lut_name, t, new_r, r0, c0), zvar in z.items():
            i = r0 + new_r
            lut_obj = next(l for l in current_level_luts if l.name == lut_name)
            for lit in lut_obj.term_products[t]:
                if lit not in ('0', '.'):
                    root = lit.lstrip('~')
                    if root not in primary_inputs:
                        m.addConstr(R[root, i] >= zvar)


        # (B) R/C ≤ sum(y), referencing lit2, the literal in the key of y
        for lit, i in R.keys():
            m.addConstr(
                R[lit, i] <= quicksum(
                    y[l, t, lit2, new_r, new_c, r0, c0]
                    for (l, t, lit2, new_r, new_c, r0, c0) in y.keys()
                    if lit2.lstrip('~') == lit and (r0 + new_r) == i
                )
            )'''
        ########################################################################


        # A C <= sum(y) constraint is not needed: the objective max(-C) minimizes C,
        # so the lower bound C >= y alone gives the exact value (same reasoning for R)


        # 4) === Warm-start injection from here ===
        # 1) Compute the seed placement with map_lut
        # --- warm start begins ---
        seed_cb = _compute_seed_with_map_lut(current_level_luts, area_VH, self.rows, self.cols, blocked)

        if not seed_cb:
            print("[warmstart] seed_cb is None -> skip warm start")
        else:
            print("[warmstart] applying map_lut-based warm start")

            for lut in current_level_luts:
                # Convention: lut.size = [w, h]
                w, h = lut.size[0], lut.size[1]

                # 1) Find the IR origin and snap it (to a band candidate)
                r0_seed, c0_seed = _find_origin_from_ir(seed_cb.grid, lut)
                if r0_seed is None or c0_seed is None:
                    print(f"[warmstart] {lut.name}: origin not found in seed -> skip")
                    continue

                # If _snap_to_candidate expects (h, w), pass (lut.size[1], lut.size[0])
                r0_snap, c0_snap = _snap_to_candidate(r0_seed, c0_seed, w, h, area_VH, self.rows, self.cols)
                if (lut.name not in candidate) or ((r0_snap, c0_snap) not in candidate[lut.name]):
                    print(f"[warmstart] {lut.name}: snapped origin not in candidate -> skip")
                    continue

                # 2) x Start: exactly one
                key_x = (lut.name, r0_snap, c0_snap)
                if key_x in x:
                    x[key_x].Start = 1.0
                    print(f"[warmstart] x[{lut.name},{r0_snap},{c0_snap}].Start = 1.0")

                # 3) Consistent z/y/R/C Start values
                #    - first choose a single new_r_sel (row) for each term t
                #    - set Start only on the y entries for that new_r_sel
                #    - also set Start=1 on the R/C implied by those y entries
                rows_used_by_root = {}   # root -> set(rows)
                cols_used_by_root = {}   # root -> set(cols)

                for t in range(h - 1):
                    # (a) infer the row (new_r) of t from the seed
                    new_r_sel = _infer_term_row_from_grid(seed_cb.grid, lut, r0_snap, c0_snap, t)

                    # (b) set Start=1.0 on z if possible (exactly one)
                    if new_r_sel is not None:
                        key_z = (lut.name, t, new_r_sel, r0_snap, c0_snap)
                        if key_z in z:
                            z[key_z].Start = 1.0
                            # debug
                            # print(f"[warmstart] z{key_z}.Start = 1.0")

                    # (c) set y only at (t, new_r_sel)
                    for lit in (L for L in lut.term_products[t] if L not in ('0', '.')):
                        pos = _find_literal_pos(seed_cb.grid, lut, t, lit, r0_snap, c0_snap)
                        if not pos:
                            continue
                        nr, nc = pos
                        # if z chose a new_r and this one differs from it, do not set y
                        if (new_r_sel is not None) and (nr != new_r_sel):
                            continue

                        key_y = (lut.name, t, lit, nc, r0_snap, c0_snap)  # y has no row index
                        if key_y in y:
                            y[key_y].Start = 1.0
                            # debug
                            # print(f"[warmstart] y{key_y}.Start = 1.0")

                            # (d) R/C Start consistent with y (by the sign-stripped root)
                            root = lit.lstrip('~')
                            # R: global row
                            i = r0_snap + nr
                            # C: global column
                            j = c0_snap + nc

                            if root not in rows_used_by_root:
                                rows_used_by_root[root] = set()
                            if root not in cols_used_by_root:
                                cols_used_by_root[root] = set()
                            rows_used_by_root[root].add(i)
                            cols_used_by_root[root].add(j)

                # (e) apply the accumulated R/C Start values
                for root, rset in rows_used_by_root.items():
                    for i in rset:
                        key_R = (root, i)
                        if key_R in R:
                            R[key_R].Start = 1.0
                for root, cset in cols_used_by_root.items():
                    for j in cset:
                        key_C = (root, j)
                        if key_C in C:
                            C[key_C].Start = 1.0

        # (Important) update once after setting Start
        m.update()
        # === 4) Objective ===
        #alpha = 1.0
        #beta  = 1.0
        obj = []
        # penalty weights for each global row/column used by a literal
        lambdaR = 1.0
        lambdaC = 1.0

        obj += [  # reachable rows/columns only
            -lambdaR * R[lit, i]
            for lit in lit_names
            for i in reachable_rows[lit]
        ]
        obj += [
            -lambdaC * C[lit, j]
            for lit in lit_names
            for j in reachable_cols[lit]
        ]

        m.setObjective(quicksum(obj), GRB.MAXIMIZE)

        # === 5) Optimization ===
        # === PASS loop: up to three passes with different Gurobi settings ===
        def _apply_params(model, params: dict):
            for k, v in params.items():
                model.setParam(k, v)

        # A TimeLimit set earlier (e.g. 60) is overridden by the per-pass TimeLimit below.
        # OutputFlag and other parameters are left unchanged.
        pass_plan = [
            # pass 1: find a solution quickly
            ({"MIPFocus": 1, "Heuristics": 0.5, "TimeLimit": 120.0}, "pass1"),
            # pass 2: stronger cuts/bounds
            ({"MIPFocus": 3, "Heuristics": 0.1, "Cuts": 2, "TimeLimit": 120.0}, "pass2"),
            # pass 3: focus on proving optimality/bounds
            ({"MIPFocus": 2, "Heuristics": 0.05, "TimeLimit": 120.0}, "pass3"),
        ]
        _b = getattr(self, "_budget", None)
        if _b:  # a level solved in parts: this part's share of the level time budget
            _tl = max(self._PART_MIN_TL, (_b["deadline"] - time.time()) / max(_b["left"], 1))
            pass_plan = [(dict(p, TimeLimit=_tl), n) for p, n in pass_plan]
            _b["left"] -= 1
        sol_found = False
        for params, _name in pass_plan:
            # If the model is structurally infeasible, changing the pass does not help, so exit immediately
            # (no need to check before the first optimize call)
            _apply_params(m, params)
            t0 = time.perf_counter()
            m.optimize()
            record_ilp_time("ILP1_map_lut", t0)

            # If the model itself is infeasible, there is no point in trying further
            if m.Status == GRB.INFEASIBLE:
                return False

            # Whether or not the time limit was hit, stop at this pass if a solution exists (no further pass)
            if m.SolCount and m.SolCount > 0:
                sol_found = True
                break
            # If no solution was found, go to the next pass

        if not sol_found:
            # No pass found a solution -> signal failure to the caller
            return False



        # ----- Stage-2 ILP: vertical shift optimization per group -----
        from gurobipy import Model as _M2, GRB as _GRB2, quicksum as _qsum2
        from collections import defaultdict
        # (Important) do not access .X if stage 1 has no solution
        if m.SolCount == 0:
            return False
        # 0) parameters
        MAX_SHIFT = 2     # group shift window (adjust/parameterize if needed)
        LAMBDA_L = 0.0    # LUT-group shift penalty |e|
        LAMBDA_T = 0.0    # term-row-group shift penalty |d|

        R, C = self.rows, self.cols

        def _is_ir_cell(val: str) -> bool:
            if not val or '_' not in val: return False
            a, b = val.split('_', 1)
            return a.lstrip('~') == b.lstrip('~')  # "{X}_~X" (or "~X_X") pattern

        # 1) scan the crossbar -> IR usage indicator (root -> set(rows))
        IR_rows_by_root = defaultdict(set)
        for i in range(R):
            for j in range(C):
                v = self.grid[i][j]
                if _is_ir_cell(v):
                    a, b = v.split('_', 1)
                    root = a.lstrip('~')  # both sides have the same root
                    IR_rows_by_root[root].add(i)

        # 2) summary of the stage-1 solution: (r0*, c0*) per LUT, new_r* per term
        chosen_r0 = {}
        chosen_c0 = {}
        for l in current_level_luts:
            for (r0, c0) in candidate[l.name]:
                if x[l.name, r0, c0].X > 0.5:
                    chosen_r0[l.name] = r0
                    chosen_c0[l.name] = c0
                    break
            # fallback (rare, but a safeguard)
            if l.name not in chosen_r0:
                # pick the largest value
                r0, c0, best = None, None, -1.0
                for (rr, cc) in candidate[l.name]:
                    val = x[l.name, rr, cc].X
                    if val > best:
                        best, r0, c0 = val, rr, cc
                chosen_r0[l.name] = r0
                chosen_c0[l.name] = c0

        # new_r* per term
        chosen_new_r = {}
        for l in current_level_luts:
            h = l.size[1]
            r0_sel = chosen_r0[l.name]
            c0_sel = chosen_c0[l.name]
            for t in range(h - 1):
                nr = None
                for new_r in range(h - 1):
                    if z[l.name, t, new_r, r0_sel, c0_sel].X > 0.5:
                        nr = new_r
                        break
                if nr is None:
                    # pick the largest z as a correction
                    best = -1.0; best_r = 0
                    for new_r in range(h - 1):
                        val = 0.0
                        for (rr, cc) in candidate[l.name]:
                            val = max(val, z[l.name, t, new_r, rr, cc].X)
                        if val > best:
                            best, best_r = val, new_r
                    nr = best_r
                chosen_new_r[(l.name, t)] = nr

        # 3) build the groups
        # 3-1) LUT groups: LUTs sharing the same r0*
        lut_groups = defaultdict(list)  # gid -> [lut]
        for l in current_level_luts:
            gid = chosen_r0[l.name]   # group ID = stage-1 r0*
            lut_groups[gid].append(l)

        # 3-2) term-row groups: terms sharing the same global row i0 = r0* + new_r*
        term_groups = defaultdict(list)  # pid -> [(l,t)]
        for l in current_level_luts:
            h = l.size[1]
            for t in range(h - 1):
                i0 = chosen_r0[l.name] + chosen_new_r[(l.name, t)]
                term_groups[i0].append((l, t))

        # 3-3) root weights W_{p,ρ} per term-row group
        W = defaultdict(lambda: defaultdict(int))  # pid -> root -> count
        for pid, lt_list in term_groups.items():
            for (l, t) in lt_list:
                # count the non-PI literal roots of term t
                row_literals = []
                if hasattr(l, "term_products") and t < len(l.term_products)-1:
                    row_literals = l.term_products[t]
                for lit in row_literals:
                    if lit in ('0', '.', None): 
                        continue
                    root = lit.lstrip('~')
                    if root in primary_inputs:
                        continue
                    W[pid][root] += 1

        # 3-4) horizontal conflict pairs (LUT pairs whose column intervals overlap)
        overlap_pairs = []
        def _intervals_overlap(a0, a1, b0, b1):
            return not (a1 < b0 or b1 < a0)

        for i in range(len(current_level_luts)):
            li = current_level_luts[i]
            ci0 = chosen_c0[li.name]; wi = li.size[0]
            for j in range(i+1, len(current_level_luts)):
                lj = current_level_luts[j]
                cj0 = chosen_c0[lj.name]; wj = lj.size[0]
                if _intervals_overlap(ci0, ci0+wi-1, cj0, cj0+wj-1):
                    overlap_pairs.append((li, lj))

        # 4) candidate shift sets (boundary + window)
        E = {}  # gid -> list of e
        gid_main_h = {}  # representative VS of each gid, for the ordering constraints in step B

        for gid, luts_in_g in lut_groups.items():
            # Start with a wide global range for e and narrow it by each LUT's own VS band constraint.
            low = -10**9; high = 10**9

            # Record the representative (most frequent) VS: used in step B for ordering constraints among groups of the same VS
            hs = [l.size[1] for l in luts_in_g]
            if hs:
                gid_main_h[gid] = Counter(hs).most_common(1)[0][0]
            else:
                gid_main_h[gid] = 2  # default to guard against abnormal cases

            for l in luts_in_g:
                r0 = chosen_r0[l.name]
                h  = l.size[1]

                # 1) get the allowed r0 range in the LUT's own VS band and convert it into an e range
                band_lo, band_hi = _vs_band_bounds(h, area_VH)  # r0 ∈ [band_lo, band_hi]
                low  = max(low,  band_lo - r0)
                high = min(high, band_hi  - r0)

                # 2) keep the crossbar boundary (existing constraint) as well
                low  = max(low,  -r0)
                high = min(high, R - h - r0)

            # 3) apply the window limit if set
            if MAX_SHIFT is not None:
                low  = max(low,  -MAX_SHIFT)
                high = min(high,   MAX_SHIFT)

            # 4) if infeasible, fix the group (e=0)
            if low > high:
                low = high = 0

            E[gid] = list(range(low, high+1))

        D = {}  # pid -> list of d
        for pid in term_groups.keys():
            low = -pid
            high = R - 1 - pid
            if MAX_SHIFT is not None:
                low  = max(low,  -MAX_SHIFT)
                high = min(high,  MAX_SHIFT)
            D[pid] = [d for d in range(low, high+1)]

        # 5) build the stage-2 MILP
        m2 = _M2("row_group_refine")
        m2.setParam('Threads', _args.threads)
        m2.setParam('TimeLimit', 60)
        m2.setParam('OutputFlag', 0)
        if _args.mipgap is not None:  # MIP gap
            m2.setParam('MIPGap', _args.mipgap)

        # variables: LUT-group choice w_{g,e}, term-row-group choice z_{p,d}
        w2 = {}  # (gid, e) -> bin
        for gid, elist in E.items():
            for e in elist:
                w2[(gid, e)] = m2.addVar(vtype=_GRB2.BINARY, name=f"w[{gid},{e}]")

        z2 = {}  # (pid, d) -> bin
        for pid, dlist in D.items():
            for d in dlist:
                z2[(pid, d)] = m2.addVar(vtype=_GRB2.BINARY, name=f"z[{pid},{d}]")

        m2.update()

        # one-of-N
        for gid, elist in E.items():
            if elist:
                m2.addConstr(_qsum2(w2[(gid, e)] for e in elist) == 1)
        for pid, dlist in D.items():
            if dlist:
                m2.addConstr(_qsum2(z2[(pid, d)] for d in dlist) == 1)

        # link constraint: δ_p == Δ_{g(l)}  (term (l,t) ∈ p)
        #   Σ d z_{p,d}  ==  Σ e w_{g,e}
        # (added only once per (p,g) pair)
        linked_pg = set()
        for pid, lt_list in term_groups.items():
            # LUT groups that the terms of pid belong to
            gids = set(chosen_r0[l.name] for (l, t) in lt_list)
            for gid in gids:
                key = (pid, gid)
                if key in linked_pg: 
                    continue
                linked_pg.add(key)
                left  = _qsum2(d * z2[(pid, d)] for d in D[pid]) if D[pid] else 0
                right = _qsum2(e * w2[(gid, e)] for e in E[gid]) if E[gid] else 0
                m2.addConstr(left == right)

        # 1) bucket the gids by VS
        vs_to_gids = {}
        for gid, main_h in gid_main_h.items():
            vs_to_gids.setdefault(main_h, []).append(gid)

        # 2) sort the gids of each VS bucket in ascending order (initial r0* order) and add ordering/non-overlap inequalities for adjacent pairs
        for h, gids in vs_to_gids.items():
            if not gids: 
                continue
            gids_sorted = sorted(gids)  # gid == initial r0*; smaller means a higher row
            for i in range(len(gids_sorted) - 1):
                gi = gids_sorted[i]
                gj = gids_sorted[i+1]

                # (the representative VS=h is used as the height of block gi) — the LUTs of a group may not all have the same VS,
                # but enforcing minimum non-overlap by the representative VS is practically sufficient and conservative.
                # r0_i + e_i + h ≤ r0_j + e_j
                lhs = gi + _qsum2(e * w2[(gi, e)] for e in E.get(gi, [])) + h
                rhs = gj + _qsum2(e * w2[(gj, e)] for e in E.get(gj, []))
                m2.addConstr(lhs <= rhs)

        # vertical non-overlap of horizontally overlapping LUT pairs + keep their original order
        for (li, lj) in overlap_pairs:
            gi = chosen_r0[li.name]; gj = chosen_r0[lj.name]
            ri0 = chosen_r0[li.name]; hi = li.size[1]
            rj0 = chosen_r0[lj.name]; hj = lj.size[1]
            if ri0 + hi <= rj0:
                m2.addConstr( (ri0 + _qsum2(e*w2[(gi,e)] for e in E[gi]) + hi)
                            <= (rj0 + _qsum2(e*w2[(gj,e)] for e in E[gj])) )
            elif rj0 + hj <= ri0:
                m2.addConstr( (rj0 + _qsum2(e*w2[(gj,e)] for e in E[gj]) + hj)
                            <= (ri0 + _qsum2(e*w2[(gi,e)] for e in E[gi])) )
            # (no constraint needed if they did not originally overlap)


        # objective: Σ_{p,d} ( Σ_ρ W_{p,ρ} * IR_{ρ, i0_p + d} ) * z_{p,d}  -  λ*|shift|
        obj_terms = []
        for pid, dlist in D.items():
            for d in dlist:
                score = 0
                for rho, cnt in W[pid].items():
                    if (pid + d) in IR_rows_by_root.get(rho, set()):
                        score += cnt
                if score != 0:
                    obj_terms.append(score * z2[(pid, d)])

        # shift penalty (optional)
        if LAMBDA_L != 0.0:
            for gid, elist in E.items():
                for e in elist:
                    if e != 0:
                        obj_terms.append(-LAMBDA_L * abs(e) * w2[(gid, e)])
        if LAMBDA_T != 0.0:
            for pid, dlist in D.items():
                for d in dlist:
                    if d != 0:
                        obj_terms.append(-LAMBDA_T * abs(d) * z2[(pid, d)])

        m2.setObjective(_qsum2(obj_terms), _GRB2.MAXIMIZE)
        ##################################################################
        t1 = time.perf_counter()
        # When a sub-level is placed in parts (size-based split), the row-group refinement is skipped:
        # its vertical shifts can move LUTs off the tile grid onto tiles a later part needs. Without a
        # solution every shift stays 0, as when ILP2 finds none.
        if not getattr(self, "_no_refine", False):
            m2.optimize()
        record_ilp_time("ILP2_row_group_refine", t1)
        ##################################################################
        # extract the solution: row shift per LUT
        row_shift_by_lut = defaultdict(int)
        # (Important) access .X only if stage 2 also has a solution
        if m2.Status != _GRB2.INFEASIBLE and m2.SolCount > 0:
            delta_by_gid = {}
            for gid, elist in E.items():
                chosen_e = 0
                best = -1.0
                for e in elist:
                    val = w2[(gid, e)].X    # ← .X is accessed from here
                    if val > 0.5:
                        chosen_e = e
                        break
                    if val > best:
                        best = val
                        chosen_e = e
                delta_by_gid[gid] = chosen_e

            for l in current_level_luts:
                gid = chosen_r0[l.name]
                row_shift_by_lut[l.name] = delta_by_gid.get(gid, 0)
        else:
            # no solution: proceed without shifting
            row_shift_by_lut = defaultdict(int)


        # Debug: check ranges that may leave the VS band
        for gid, elist in E.items():
            # representative VS and r0* range of the gid
            h = gid_main_h[gid]
            b_lo, b_hi = _vs_band_bounds(h, area_VH)
            lo_e, hi_e = (min(elist) if elist else 0), (max(elist) if elist else 0)
            r0_min = gid + lo_e
            r0_max = gid + hi_e
            if r0_min < b_lo or r0_max > b_hi:
                print(f"[WARN] gid={gid} VS=V{h}: e∈[{lo_e},{hi_e}] → r0'∈[{r0_min},{r0_max}] may exceed band [{b_lo},{b_hi}]")
        # ----- end of stage-2 ILP -----


        # === 6) apply the result and place ===
        # no solution means nothing to read, so return failure immediately
        if m.SolCount == 0:
            return False
        for lut in current_level_luts:
            w, h = lut.size
            # determine the origin
            for (r0, c0) in candidate[lut.name]:
                if x[lut.name, r0, c0].X > 0.5:
                    shifted_r0 = r0 + row_shift_by_lut.get(lut.name, 0)
                    lut._alloc_origin = (shifted_r0, c0)
                    break
            # reorder term_products (ILP placement + primary inputs filled left to right)
            new_grid = []
            for t, lits in enumerate(lut.term_products[:-1]):
                perm = ['0'] * w
                # a) literals included in the ILP model
                ilp_lits = [
                    lit for lit in lits
                    if lit != '0' and lit.lstrip('~') not in primary_inputs
                ]
                # b) primary inputs (in their original order)
                pi_values = [
                    lit for lit in lits
                    if lit != '0' and lit.lstrip('~') in primary_inputs
                ]

                # Read-out of the ILP solution (y has no row index)
                # (i) take the column directly from y
                for lit in ilp_lits:
                    placed = False
                    for new_c in range(w - 1):
                        if any(
                            y[lut.name, t, lit, new_c, r0, c0].X > 0.5
                            for (r0, c0) in candidate[lut.name]
                        ):
                            perm[new_c] = lit
                            placed = True
                            break
                    # fallback: with a large MIP gap there may be no y > 0.5
                    if not placed:
                        best_val, best_c = -1.0, 0
                        for new_c in range(w - 1):
                            for (r0, c0) in candidate[lut.name]:
                                val = y[lut.name, t, lit, new_c, r0, c0].X
                                if val > best_val and perm[new_c] == '0':
                                    best_val, best_c = val, new_c
                        if best_val > 0:
                            perm[best_c] = lit
                # (ii) fill the remaining '0' slots with primary inputs from left to right
                pi_iter = iter(pi_values)
                for pos in range(w):
                    if perm[pos] == '0':
                        try:
                            perm[pos] = next(pi_iter)
                        except StopIteration:
                            break

                new_grid.append(perm)
            
            new_grid.append(lut.term_products[-1])
            lut.term_products = new_grid
            # actual placement
            r0, c0 = lut._alloc_origin
            self.place_lut_block(lut, r0, c0)
            for t in range(h - 1):
                for c_idx, net in enumerate(lut.term_products[t]):
                    if net != '.':
                        self.place_term_instance(lut, t, c_idx, net)

        return True

#######A* definition#######
def g(r, c, Rd, Cd, start_polarity, destination_polarity, crossbar):
    if isinstance(destination_polarity, str):
        if (r == Rd or c == Cd):
            destination_polarity = 1-start_polarity
        elif (r != Rd and c != Cd):
            destination_polarity = start_polarity

    if (r, c) == (Rd, Cd):
        return 0, destination_polarity
    elif (r == Rd or c == Cd) and (start_polarity != destination_polarity):
        return 1, destination_polarity
    
    elif (r == Rd or c == Cd) and (start_polarity == destination_polarity):
        return 2, destination_polarity
    
    elif (r != Rd and c != Cd) and (start_polarity == destination_polarity):
        if crossbar.grid[Rd][c] or crossbar.grid[r][Cd] == None:
            return 2, destination_polarity
        else: return 4, destination_polarity
    

    elif (r != Rd and c != Cd) and (start_polarity != destination_polarity):
        #if crossbar.grid[Rd][c] or crossbar.grid[r][Cd] == None:
            return 3, destination_polarity
        #else: return 5, destination_polarity

def get_neighbors(r, c, Rd, Cd, crossbar, source_polarity, destination_polarity, source, A_star_idx):
    neighbors = []
    #col_neighbor = crossbar.grid[Rd][Cd].split("_")[1]
    #if col_neighbor[0] == "~":
     #   col_neighbor[1:]
    #if crossbar.grid[r][c] is not None:
     #   if "_" in crossbar.grid[r][c]:
      #      IR = crossbar.grid[r][c].split("_")[1]
       #     if IR[0] == "~":
        #        IR = IR[1:]

    if  c == Cd:       
        if A_star_idx == 1 and (r,c) == source or crossbar.grid[r][c] is None:
            if source_polarity != destination_polarity: #total path length = even
                if A_star_idx % 2 == 1:
                    neighbors.append((Rd,Cd))
                    return neighbors
                else: 
                    for i in range(len(crossbar.grid)):
                        if crossbar.grid[i][c] is None and i != r:
                            neighbors.append((i,Cd))
                            return neighbors
            else:
                if A_star_idx % 2 == 1:
                    for i in range(len(crossbar.grid)):
                        if crossbar.grid[i][c] is None and i != r:
                            neighbors.append((i,Cd))
                            return neighbors
                else: 
                    neighbors.append((Rd,Cd))
                    return neighbors
    if r == Rd:
        #if A_star_idx == 1 and (r,c) == source and "_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[1][-2:] ==  crossbar.grid[r][c].split("_")[0][-2:]or 
        if A_star_idx == 1 and (r,c) == source or crossbar.grid[r][c] is None:
            if source_polarity != destination_polarity: #total path length = odd
                if A_star_idx % 2 == 1:
                    neighbors.append((Rd,Cd))
                    return neighbors
                else: 
                    for i in range(len(crossbar.grid[0])):
                        if crossbar.grid[r][i] is None and i != c:
                            neighbors.append((Rd,i))
                            return neighbors
            else:
                if A_star_idx % 2 == 1:
                    for i in range(len(crossbar.grid[0])):
                        if crossbar.grid[r][i] is None and i != c:
                            neighbors.append((Rd,i))
                            return neighbors
                else: 
                    neighbors.append((Rd,Cd))
                    return neighbors
                
    if crossbar.grid[r][Cd] is None:
        if source_polarity == destination_polarity: #total path length = odd
            if A_star_idx % 2 == 1:
                neighbors.append((r,Cd))
                return neighbors
            else: 
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[r][i] is None and i != c:
                        neighbors.append((r,i))
                        return neighbors
        else:
            if A_star_idx % 2 == 1:
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[r][i] is None and i != c:
                        neighbors.append((r,i))
                        return neighbors
            else: 
                neighbors.append((r,Cd))
                return neighbors
            
    if crossbar.grid[Rd][c] is None:
        if source_polarity == destination_polarity: #total path length = odd
            if A_star_idx % 2 == 1:
                neighbors.append((Rd,c))
                return neighbors
            else: 
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[i][c] is None and i != r:
                        neighbors.append((i,c))
                        return neighbors
        else:
            if A_star_idx % 2 == 1:
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[i][c] is None and i != r:
                        neighbors.append((i,c))
                        return neighbors
            else: 
                neighbors.append((Rd,c))
                return neighbors

    neighbors2 = []
    if A_star_idx % 2 == 1:
        for i in range(len(crossbar.grid[0])):
            if crossbar.grid[r][i] is None:
                if crossbar.grid[Rd][i] is None or crossbar.grid[r][Cd] is None:
                    neighbors.append((r, i))
                    neighbors2.append((r, i))
                else:
                    neighbors2.append((r, i))

        if len (neighbors) >= 10: 
            return neighbors

        for i in range(len(crossbar.grid)):
            if crossbar.grid[i][c] is None:
                neighbors2.append((i, c))
    else:
        for i in range(len(crossbar.grid)):
            if crossbar.grid[i][c] is None:

                if crossbar.grid[Rd][c] is None or crossbar.grid[i][Cd] is None:
                    neighbors.append((i, c))
                    neighbors2.append((i, c))
                else:
                    neighbors2.append((i, c))

        if len (neighbors) >= 10: 
            return neighbors

        for i in range(len(crossbar.grid[0])):
            if crossbar.grid[r][i] is None:
                neighbors2.append((r,i))

    return neighbors2


import heapq
from functools import lru_cache

import heapq
import time
from functools import lru_cache

def _A_star_search_heur(source, destination, source_polarity, destination_polarity, crossbar):
    """
    Optimized version:
      - state (r,c,pol) encoded as an int
      - neighbor bulk push + heapify to minimize heapq operations
      - heuristic fallback is an inline Manhattan distance
    """
    global place_info_dict
    Rs, Cs = source
    Rd, Cd = destination
    R, C = len(crossbar.grid), len(crossbar.grid[0])

    # -------- State encode/decode --------
    def encode_state(r, c, pol):
        return ((r * C) + c) << 1 | pol

    def decode_state(state_id):
        pol = state_id & 1
        idx = state_id >> 1
        r, c = divmod(idx, C)
        return r, c, pol

    # -------- Neighbor cache --------
    @lru_cache(maxsize=None)
    def cached_neighbors(r, c, path_rev):
        raw = get_neighbors(r, c, Rd, Cd, crossbar,source_polarity, destination_polarity, source, path_rev)
        seen = set()
        cleaned = []
        for nr, nc in raw:
            if (nr, nc) == (r, c):
                continue
            if (nr, nc) not in seen:
                seen.add((nr, nc))
                cleaned.append((nr, nc))
        return tuple(cleaned)

    # -------- Heuristic --------
    if 'g' in globals():
        @lru_cache(maxsize=None)
        def heuristic(nr, nc, pol):
            h, _ = g(nr, nc, Rd, Cd, pol, destination_polarity, crossbar)
            return h
    else:
        @lru_cache(maxsize=None)
        def heuristic(nr, nc, pol):
            return abs(nr - Rd) + abs(nc - Cd)

    # -------- Path reconstruction --------
    def reconstruct_path(came_from, end_state_id):
        path_rev = []
        cur = end_state_id
        while cur is not None:
            r, c, pol = decode_state(cur)
            path_rev.append(((r, c), pol))
            cur = came_from.get(cur, None)
        path_rev.reverse()
        return path_rev

    # -------- Init --------
    log_section_time("A_star_search::section1 init", time.time())
    open_heap = []
    start_id = encode_state(Rs, Cs, source_polarity)
    start_h = heuristic(Rs, Cs, source_polarity)
    heapq.heappush(open_heap, (start_h, 0, start_id))

    closed_set = set()
    came_from = {start_id: None}
    gscore = {start_id: 0}

    # -------- Search --------
    while open_heap:
        t0 = time.time()
        priority, gcur, state_id = heapq.heappop(open_heap)
        log_section_time("A_star_search::section2-1", t0)

        if state_id in closed_set:
            continue
        closed_set.add(state_id)

        r, c, pol = decode_state(state_id)

        # goal test
        if (r, c) == (Rd, Cd):
            same_pol = (source_polarity == destination_polarity)
            if (same_pol and (gcur % 2 == 0)) or ((not same_pol) and (gcur % 2 == 1)):
                path_rev = reconstruct_path(came_from, state_id)
                return path_rev
        #else: path_rev = []
        # expand neighbors (optimized: heappush only)
        t3 = time.time()
        path_len = gscore[state_id] + 1
        for nr, nc in cached_neighbors(r, c, path_len):
            new_pol = 1 - pol
            nstate = encode_state(nr, nc, new_pol)
            if nstate in closed_set:
                continue

            tentative_g = gcur + 1
            prev_g = gscore.get(nstate)
            if prev_g is None or tentative_g < prev_g:
                gscore[nstate] = tentative_g
                came_from[nstate] = state_id
                h = heuristic(nr, nc, new_pol)
                f = tentative_g + h
                heapq.heappush(open_heap, (f, tentative_g, nstate))
        log_section_time("A_star_search::section6", t3)



def _exact_copy_route(source, destination, source_polarity, destination_polarity, crossbar):
    """Complete search, used only when the heuristic A* finds no route.
    Same move model as get_neighbors: one copy (MAGIC NOT) moves a value to any empty cell in the
    same row or column and flips its polarity; the destination is the fan-in cell. Breadth-first
    search over (cell, move parity) returns a route with the fewest copies whose parity matches the
    required polarity, in A_star_search's format [((r, c), polarity), ...], or None if none exists."""
    from collections import deque
    grid = crossbar.grid
    R, C = len(grid), len(grid[0])
    Rs, Cs = source
    Rd, Cd = destination
    need = 0 if source_polarity == destination_polarity else 1
    start = (Rs, Cs, 0)
    prev = {start: None}
    dq = deque([start])
    while dq:
        r, c, par = dq.popleft()
        npar = 1 - par
        cand = [(r, j) for j in range(C) if j != c] + [(i, c) for i in range(R) if i != r]
        for nr, nc in cand:
            if (nr, nc) == (Rd, Cd):
                if npar == need:
                    route = [(nr, nc, npar)]
                    cur = (r, c, par)
                    while cur is not None:
                        route.append(cur)
                        cur = prev[cur]
                    route.reverse()
                    return [((rr, cc), source_polarity ^ pp) for rr, cc, pp in route]
                continue
            if grid[nr][nc] is None:
                st = (nr, nc, npar)
                if st not in prev:
                    prev[st] = (r, c, par)
                    dq.append(st)
    return None


def A_star_search(source, destination, source_polarity, destination_polarity, crossbar):
    """Heuristic A* first (paper Sec. III-D); complete search only when it finds no route."""
    path = _A_star_search_heur(source, destination, source_polarity, destination_polarity, crossbar)
    if path:
        return path
    path = _exact_copy_route(source, destination, source_polarity, destination_polarity, crossbar)
    print(f"[A* fallback] heuristic A* found no route {source}->{destination}; complete search: "
          f"{'found ' + str(len(path) - 1) + ' copies' if path else 'no route exists'}")
    return path


#######Find the fanin, find where to fetch it from, route it with A* and map it#######
def mapping_A_star(path, lut, crossbar, destination_polarity, source_polarity):
    #crossbar = mapping_A_star(path, fanin, crossbar, opt_destination_polarity, start_polarity)
    global Attempt
    global Cycle
    global Path
    global A_star_Cycle
    
    if path is None:
        return crossbar 
    

    for idx, ((r, c),_) in enumerate(path):
        #if idx == 0 and '_' not in crossbar.grid[r][c]:
        #    if crossbar.grid[r][c][0] == "~": 
        #        source_polarity = 0   #0:~ 1:as is

        #    else: 
        #        source_polarity = 1

        if idx == 0:
            if "_" in crossbar.grid[r][c]: 
                even_source = crossbar.grid[r][c].split("_")[1]
            else:
                even_source = crossbar.grid[r][c]

            if even_source[0] == "~":
                odd_source = even_source[1:]
            else:
                odd_source = "~" + even_source



        if idx > 0 and idx < len(path):
            check1 = int(path[idx-1][0][0])
            check2 = int(path[idx-1][0][1])
            # The file-open block must keep this indentation.
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                data = "C%d Copy LUT%s (%d, %d) to (%d, %d)\n" % (Cycle, lut, check1, check2, r, c)
                f.write(data)
                Cycle += 1  # increment Cycle after writing to the file
                A_star_Cycle += 1

        

        if idx > len(path)-1: return crossbar
        #edit ~=source polarity=0

        elif idx > 0 and  idx < len(path) and idx % 2 == 0:  #even idx
                crossbar.grid[r][c] = even_source                

        elif idx > 0 and  idx < len(path) and idx % 2 == 1: #odd idx
            #if source_polarity == 0: ##~n
             #   lut1=source[1:]
                crossbar.grid[r][c] = odd_source
            #else:
             #   crossbar.grid[r][c] = "~"+source
        
    return crossbar
    
def find_fanin(luts, crossbar,cell_content,fanin):

    for c in range(len(crossbar.grid[0]) - 1, -1, -1):
        for r in range(len(crossbar.grid)):
            #if row+r > len(crossbar.grid) - 1 or col+c > len(crossbar.grid[0]) - 1 : break
            if crossbar.grid[r][c] is not None and '_' in crossbar.grid[r][c] and crossbar.grid[r][c].split('_')[0] != crossbar.grid[r][c].split('_')[1]:
                
                if '_' not in crossbar.grid[r][c] : continue
                
                test_temp = crossbar.grid[r][c].split('_')
                
                cell_content2 = fanin
                if cell_content2 != test_temp[1]: break
                else: cell_content = test_temp[0]
                
                if len(test_temp) < 2: continue
                
                if (cell_content2[0]=="~" and cell_content == cell_content2[1:]) or (cell_content2[0]!="~" and cell_content == cell_content2): break

                if len(cell_content)>=2 and len(cell_content2)>=2:
                    if cell_content2[0] == "n" or cell_content2[0] == "O" or (c != luts[cell_content].col[-1] and (cell_content2[0:2] == "~n" or cell_content2[0:2] == "~O")) :
                    
                        current_level = luts[cell_content].level
                        #fanin = crossbar.grid[r][c].split('_')[1] #fanin: fanin name; name of the destination
                        if crossbar.grid[r][c].split('_')[1][0] == "~": 
                            destination_polarity = 0
                        else: 
                            destination_polarity = 1
                        #print(target) 
                        sources=find_sources(fanin, crossbar)
                        return cell_content #fanin, destination_polarity, sources, cell_content    #target: LUT that has it as fanin, excluding intermediate results
                                                            #row+r, col+c  == destination
        col=0    

def find_sources(fanin, crossbar):
    sources=[]  
    _cur_names = {l.name for l in current_level_luts2}  # built once per call
    for r in range(len(crossbar.grid)):
        for c in range(len(crossbar.grid[0])):
            #if (r,c) == location: continue
            if crossbar.grid[r][c] is not None:  
                #destination = crossbar.grid[row][col].split
                #if destination 

                # A stored IR may sit left of the 75% column (many IRs, e.g. div 256x256); accept an IR
                # anywhere unless it is the (not yet computed) output cell of a LUT of the current level
                if '_' in crossbar.grid[r][c] and (c >= int((len(crossbar.grid[0])) * 0.75) or
                        crossbar.grid[r][c].split('_')[0].lstrip('~') not in _cur_names):
                #if fanin[0] == "~" and (crossbar.grid[r][c][-4:] == (fanin or fanin[1:]) or crossbar.grid[r][c][-3:] == fanin[1:]) or crossbar.grid[r][c][-3:] == fanin or crossbar.grid[r][c][-4:] == fanin or crossbar.grid[r][c][-5:] == fanin: 
                    #if fanin[0] == "~" and (crossbar.grid[r][c].split("_")[1] == fanin[1:]) or fanin[0] != "~" and (crossbar.grid[r][c].split("_")[1] == fanin):
                            if fanin[0] != "~" and (crossbar.grid[r][c].split("_")[1]== "~" + fanin or crossbar.grid[r][c].split("_")[1]== fanin) or fanin[0] == "~" and (crossbar.grid[r][c].split("_")[1] == fanin or crossbar.grid[r][c].split("_")[1] == fanin[1:]):
                                cell_content = crossbar.grid[r][c].split('_')[0]
                                if cell_content[0] == "~":
                                    cell_content = cell_content[1:]
                            
                                cell_content2 = crossbar.grid[r][c].split("_")[1]
                                if cell_content2[0] == "~":
                                    cell_content2 = cell_content2[1:]


                                if cell_content == cell_content2: #saved IR    
                                    if crossbar.grid[r][c].split('_')[1][0] == "~": 
                                        source_polarity = 0   #0:~ 1:
                                        sources.append(((r,c),fanin, source_polarity))

                                    else: 
                                        source_polarity = 1
                                        sources.append(((r,c),fanin, source_polarity))
                                #else: #placed
                                 #   if luts[cell_content].level == current_level and (row > r and col > c) :
                                  #      if crossbar.grid[r][c].split('_')[1][0] == "~": 
                                   #         source_polarity = 0   #0:~ 1:
                                    #        sources.append(((r,c),fanin, source_polarity))

                                     #   else: 
                                      #      source_polarity = 1
                                       #     sources.append(((r,c),fanin, source_polarity))           


                elif '_' not in crossbar.grid[r][c]: # = A*
                    if fanin[0] == "~" and (crossbar.grid[r][c]== "~" + fanin or crossbar.grid[r][c]== fanin) or fanin[0] != "~" and (crossbar.grid[r][c] == fanin or crossbar.grid[r][c] == fanin[1:]):
                        if crossbar.grid[r][c][0] == "~": 
                            source_polarity = 0   #0:~ 1:as is
                            sources.append(((r,c),fanin, source_polarity))

                        else: 
                            source_polarity = 1
                            sources.append(((r,c),fanin, source_polarity))  #((position), lut name, source_polarity)
    return sources
 

def get_common_coords(y_shared, x_shared):
    x_flat_set = set(coord for group in x_shared for coord in group)
    result = []
    for group in y_shared:
        common = [coord for coord in group if coord in x_flat_set]
        result.append(common)
    if result:
        return result
    return None


def group_generation(source):
    from collections import defaultdict

    # group by x and y coordinates
    y_dict = defaultdict(list)
    x_dict = defaultdict(list)

    for ((x, y), polarity) in source:
        y_dict[y].append((x, y))
        x_dict[x].append((x, y))

    # sort within each group
    for k in y_dict:
        y_dict[k].sort()  # for (x, y): ascending by x
    for k in x_dict:
        x_dict[k].sort()  # for (x, y): ascending by y

    # keep only groups of size 2 or more
    y_shared = {k: v for k, v in y_dict.items() if len(v) >= 2}
    x_shared = {k: v for k, v in x_dict.items() if len(v) >= 2}

    if not y_shared and not x_shared:
        return None

    # sort groups: by size descending, then by key ascending
    y_sorted = sorted(y_shared.items(), key=lambda item: (-len(item[1]), item[0]))
    x_sorted = sorted(x_shared.items(), key=lambda item: (-len(item[1]), item[0]))

    print("groups with the same y:", [v for _, v in y_sorted])
    print("groups with the same x:", [v for _, v in x_sorted])

    max_y_len = 0
    max_x_len = 0

    # group selection criterion
    for i in range(len(y_sorted)):
        if max_y_len < len(y_sorted[i]):
            max_y_len = len(y_sorted[i])

    for j in range(len(x_sorted)):
        if max_x_len < len(x_sorted[j]):
            max_x_len = len(x_sorted[j])

    if max_y_len > max_x_len:
        priority_group = [v for _, v in y_sorted]
        axis_flag = 1
    elif max_y_len < max_x_len:
        priority_group = [v for _, v in x_sorted]
        axis_flag = 0
    else:
        if len(y_sorted[0][1]) > len(x_sorted[0][1]):
            priority_group = [v for _, v in y_sorted]
            axis_flag = 1
        else:
            priority_group = [v for _, v in x_sorted]
            axis_flag = 0

    # return: group list + axis flag
    return priority_group, axis_flag





def optimazation_points(idx,source,priority_group,crossbar,sources):
    optimazation_points_list=[]
    optimization_points_dict = {}
    selected_priority_group = []
    optimazation_point = None

    shared_xy = int(priority_group[-1])
    for i in range(len(priority_group)-1):
        if source[idx][0][shared_xy] == priority_group[i][0][0][shared_xy]:
            selected_priority_group = priority_group[i][0]
            break

    if len(selected_priority_group) > 0:
        shared_point = selected_priority_group[0][shared_xy]
        print(shared_point)
    else: return None

    if shared_xy == 0:
        for random_c in range(len(crossbar.grid[0])):
            if crossbar.grid[shared_point][random_c] is None:
                optimazation_points_list.append((shared_point,random_c))
                if random_c == sources[0][0][1]:
                    optimazation_point = (shared_point,random_c)
                    break
        key = "r"+str(shared_point)
        if key not in optimization_points_dict:
            optimization_points_dict[key] = []
        optimization_points_dict[key].append(optimazation_points_list)
    
                
    else:
        for random_r in range(len(crossbar.grid)):
            if crossbar.grid[random_r][shared_point] is None:
                optimazation_points_list.append((random_r,shared_point))
                if random_r == sources[0][0][0]:
                    optimazation_point = (random_r,shared_point)
                    break

        key = "c"+str(shared_point)
        if key not in optimization_points_dict:
            optimization_points_dict[key] = []
        optimization_points_dict[key].append(optimazation_points_list)
    
    if len(optimization_points_dict) > 1:
        return optimization_points_dict
    if len(optimization_points_dict) == 1:
        if optimazation_point == None:
            return optimazation_points_list
        else: return optimazation_point




    return optimazation_point


from collections import Counter, defaultdict

def sort_by_common_axis(source, axis='row'):
    idx = 0 if axis == 'row' else 1
    counts = Counter([s[0][idx] for s in source])

    # step 1: grouping
    groups = defaultdict(list)
    for item in source:
        groups[item[0][idx]].append(item)

    # step 2: sort the groups (frequency first, then value ascending)
    sorted_groups = sorted(
        groups.items(),
        key=lambda x: (-counts[x[0]], x[0])
    )

    # step 3: sort within each group (ascending along the other axis)
    result = []
    for group_value, items in sorted_groups:
        items.sort(key=lambda x: x[0][1 - idx])  # sort within the group by the other axis
        result.extend(items)

    return result



#if len(past_path)
def opt_A_star_search(past_path, crossbar, fanin, start, optimazation_point, start_polarity, opt_destination_polarity, destination_polarity, destination):
    global Cycle
    global A_star_Cycle
     #find the path to the optimization point and map it

    r, c = start
    r_opt, c_opt = optimazation_point
    r_des, c_des = destination
    path = []
    shortest_path = None

    if len(past_path) != 0:
        for ((r_past, c_past),start_polarity_past) in past_path:
            if (r_past == r_des or c_past == c_des):
                if start_polarity_past == 1-destination_polarity:
                    optimazation_point2 = r_past, c_past
                    return crossbar, optimazation_point2, path, shortest_path
                else:
                    r,c = (r_past, c_past)
                    r_opt, c_opt = optimazation_point
                    start_polarity = start_polarity_past
                    opt_destination_polarity = 1-start_polarity_past

    
    if len(past_path) != 0 and (r, c) == start:
        path = A_star_search(start, destination, start_polarity, destination_polarity, crossbar)
        if len(path) > 3:
            len_shortest_path = len(path)
            shortest_path = path
            for ((r_past, c_past),start_polarity_past) in past_path:
                path = A_star_search((r_past, c_past), destination, start_polarity, destination_polarity, crossbar)
                if len(path) < len_shortest_path:
                    len_shortest_path = len(path)
                    shortest_path = path

        elif len(path) == 3:
            len_shortest_path = len(path)
            shortest_path = path
            for ((r_past, c_past),start_polarity_past) in past_path:
                if start_polarity_past == destination_polarity:
                    path = A_star_search((r_past, c_past), destination, start_polarity, destination_polarity, crossbar)
                    if len(path) < len_shortest_path:
                        len_shortest_path = len(path)
                        shortest_path = path
        



    file_path = os.path.join(Path, "instructions.txt")
    with open(file_path, 'a', encoding='utf-8') as f:
        if shortest_path is None:
            data = "C%d Copy LUT%s (%d, %d) to (%d, %d)\n" % (Cycle, fanin, r, c, r_opt, c_opt)
            f.write(data)
            Cycle += 1  # increment Cycle after writing to the file
            A_star_Cycle += 1
        else: 
            (r,c) = shortest_path[0][0]
            return crossbar, None, path, shortest_path

    path.append(((r,c),start_polarity))
    path.append(((r_opt,c_opt),opt_destination_polarity))

    if start_polarity == 1:
        if fanin[0] == "~":
            crossbar.grid[r_opt][c_opt] = fanin
        else:
            crossbar.grid[r_opt][c_opt] = "~" + fanin
    else:
        if fanin[0] == "~":
            crossbar.grid[r_opt][c_opt] = fanin[1:]
        else:
            crossbar.grid[r_opt][c_opt] = fanin

    if opt_destination_polarity == destination_polarity:
        if r_des == r_opt:
            for cc in range (len(crossbar.grid[0]) - 1, -1, -1):
                if crossbar.grid[r_des][cc] is None: #or "_" not in crossbar.grid[r_des][cc]:
                    if fanin[0] == "~":
                        crossbar.grid[r_opt][cc] = fanin[1:]
                        break
                    else:
                        crossbar.grid[r_opt][cc] = "~" + fanin
                        break

            optimazation_point2 = (r_opt, cc)
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                data = "C%d Copy LUT%s (%d, %d) to (%d, %d)\n" % (Cycle, fanin, r_opt, c_opt, r_opt, cc)
                f.write(data)
                Cycle += 1  # increment Cycle after writing to the file
                A_star_Cycle += 1
            path.append(((r_opt,cc),1-opt_destination_polarity))
            return crossbar, optimazation_point2, path, shortest_path

        elif c_des == c_opt:
            for rr in range (len(crossbar.grid)- 1, -1, -1):
                if crossbar.grid[rr][c_des] is None: #or "_" not in crossbar.grid[rr][c_des]:
                    if fanin[0] == "~":
                        crossbar.grid[rr][c_des] = fanin[1:]
                        break
                    else:
                        crossbar.grid[rr][c_des] = "~" + fanin
                        break

            optimazation_point2 = (rr, c_des)
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                data = "C%d Copy LUT%s (%d, %d) to (%d, %d)\n" % (Cycle, fanin, r_opt, c_opt, rr, c_des)
                f.write(data)
                Cycle += 1  # increment Cycle after writing to the file
                A_star_Cycle += 1
            path.append(((rr,c_opt),1-opt_destination_polarity))
            return crossbar, optimazation_point2, path, shortest_path


    return crossbar, None, path, None

def smaller_crossbar(source, crossbar):
    r_list = []
    c_list = []
    optimization_point_list = []

    for r,c in source:
        if r not in r_list:
            r_list.append(r)
    
    for r,c in source:
        if c not in c_list:
            c_list.append(c)

    for rr in r_list:
        for cc in c_list:
            if crossbar.grid[rr][cc] is None:
                optimization_point_list.append((rr,cc))

    for idx, (r,c) in enumerate (optimization_point_list):
        if r == source[-1][0][0] and c == source[-1][0][1]:
            del optimization_point_list[idx]
            break


    return optimization_point_list
        

def small_crossbar(source, crossbar):
    #if past_path
    last_c = len(crossbar.grid[0])
    last_r = len(crossbar.grid)
    first_r = len(crossbar.grid)
    first_c = len(crossbar.grid[0])
    optimization_point_list = []


    #for (r,c),destination_polarity in source:
     #   if r > last_r:
      #      last_r = r

    for (r,c),destination_polarity in source:
        if r < first_r:
            first_r = r
        
    for (r,c),destination_polarity in source:
        if c < first_c:
            first_c = c

    #source.append((sources[0][0],sources[0][2]))
    for i in range(len(source)):
        optimization_point_list.append(source[i][0])

    for (r,c),destination_polarity in source:
        for cc in range(first_c, max(1, last_c)):
            if r >= first_r:
                if crossbar.grid[r][cc] is None:
                    if (r,cc) not in optimization_point_list:
                        optimization_point_list.append((r,cc))
            
            elif r <= last_r:
                if crossbar.grid[r][cc] is None:
                    if (r,cc) not in optimization_point_list:
                        optimization_point_list.append((r,cc))
            

    
    for (r,c),destination_polarity in source:
        for rr in range(first_r, max(1, last_r)):
            if c >= first_c:
                if crossbar.grid[rr][c] is None:
                    if (rr,c) not in optimization_point_list:
                        optimization_point_list.append((rr,c))
                
            elif c <= last_c:
                if crossbar.grid[rr][c] is None:
                    if (rr,c) not in optimization_point_list:
                        optimization_point_list.append((rr,c))

    for idx, (r,c) in enumerate (optimization_point_list):
        if r == source[-1][0][0] and c == source[-1][0][1]:
            del optimization_point_list[idx]
            break

    return optimization_point_list

def cal_opt_cost(source, optimization_point_list):
    new_optimization_point_list = [] #points with the most common nodes
    past_optimazation_point_list = [] #other cases / chosen when the cases above cannot be reached

    for point in optimization_point_list:
        r_pos_point_num = 0
        r_neg_point_num = 0
        c_pos_point_num = 0
        c_neg_point_num = 0
        opt_des_pol = None
        source_check = False

        for (source_point, polarity) in source:
            if point[0] == source_point[0]:
                if point[1] == source_point[1]: 
                    source_check = True
                    source_polarity = polarity
                if polarity == 1:
                    r_pos_point_num += 1
                else:
                    r_neg_point_num += 1
            elif point[1] == source_point[1]:
                if polarity == 1:
                    c_pos_point_num += 1
                else:
                    c_neg_point_num += 1

        point_sum = r_pos_point_num + r_neg_point_num + c_pos_point_num + c_neg_point_num

        if source_check == True:
            opt_des_pol = source_polarity
            new_optimization_point_list.append((point, point_sum, opt_des_pol ,r_pos_point_num, r_neg_point_num, c_pos_point_num, c_neg_point_num))

        else:
            if point_sum > 0:
                opt_des_pol = cal_opt_polarity(r_pos_point_num, r_neg_point_num, c_pos_point_num, c_neg_point_num)
                new_optimization_point_list.append((point, point_sum, opt_des_pol ,r_pos_point_num, r_neg_point_num, c_pos_point_num, c_neg_point_num))
            #(target coordinate, overlap count, polarity, row1, row0, col1, col0)

    return new_optimization_point_list

def cal_opt_polarity(r_pos_point_num, r_neg_point_num, c_pos_point_num, c_neg_point_num):
    
    if r_pos_point_num != 0 and r_neg_point_num != 0 or c_pos_point_num != 0 and c_neg_point_num != 0: #both polarity exists in row/col
        opt_des_pol = "both" #1100 0011 1101 1110 0111 1011 1111

    elif (r_pos_point_num != 0 or c_pos_point_num != 0) and r_neg_point_num == 0 and c_neg_point_num == 0:#only pos 1010 1000 0010
        opt_des_pol = 0

    elif r_pos_point_num == 0 and c_pos_point_num == 0 and (r_neg_point_num != 0 or c_neg_point_num != 0): # only neg  0101 0100 0001
        opt_des_pol = 1

    elif (r_pos_point_num != 0 and r_neg_point_num == 0 and c_pos_point_num == 0 and c_neg_point_num != 0) or (r_pos_point_num == 0 and r_neg_point_num != 0 and c_pos_point_num != 0 and c_neg_point_num == 0):
        opt_des_pol = "both_cost" #1001 0110

    return opt_des_pol




def execute_Copy_2(luts, crossbar): 
    global place_info_dict
    place_info_dict_sorted = dict(sorted(place_info_dict.items(), key=lambda item: len(item[1]), reverse=True))
    fanin_list = list(place_info_dict_sorted.keys())

    row=0 
    col=0

    while (len(place_info_dict_sorted)):
        last_key = next(reversed(place_info_dict_sorted))
        for key,source in place_info_dict_sorted.items():  #sources : ((position), lut name, source_polarity)
            #if source in past_path: break
            if source[0][1] == 0 and key[0] != "~":
                fanin = "~" + key
            else: fanin = key

            past_path = []
            sources = find_sources(fanin, crossbar)
            past_path.append((sources[0][0],sources[0][2]))
            source2 = source.copy()
            optimization_point_path = []
            no_path = False
            
            while (len(source2) > 0):

                if len(source) > 1  and no_path == False:
                    if len(source2) == len(source): 
                        source2.append((sources[0][0],sources[0][2]))
                    optimization_point_list = small_crossbar(source2, crossbar)

                    if len(optimization_point_list) == 0:
                        optimization_point_list = small_crossbar(source2, crossbar)

                    if len(source2) == len(source) + 1:
                        del source2[-1]

                    new_optimization_point_list = cal_opt_cost(source2, optimization_point_list)
                    #(target coordinate, overlap count (sum), polarity, row/1, row/0, col/1, col/0)
                    
                    optimization_point_path = []
                    min_cost = 100
                    for s in new_optimization_point_list:
                        opt_destination_polarity = s[2]
                        Rd, Cd = s[0]
                        for (r,c), start_polarity in past_path:
                            h, n_opt_destination_polarity = g(r, c, Rd, Cd, start_polarity, opt_destination_polarity, crossbar) 
                            cost = h - s[1] #h = expected_cost, s[1] = overlap count (sum)
                            if min_cost >= cost:
                                min_cost = cost
                                optimization_point_path.append(((r, c), s[0], start_polarity, n_opt_destination_polarity, cost))
                                #start, opt_point, start_polarity, opt_destination_polarity, cost
                
                if len(source) != 1 and len(optimization_point_list) > 0 and no_path == False:
                    ## start -> opt ##
                    optimization_point_path.sort(key=lambda x: x[-1])
                    while True:
                        for idx, i in enumerate(optimization_point_path):
                            #if i[-1] == min_cost:
                                #if (i[0],i[2]) in source:
                                    min_cost = i
                                    del optimization_point_path[idx]
                                    break
                        if isinstance(min_cost, int): 
                            min_cost = min(optimization_point_path, key=lambda x: x[-1])
                        start = min_cost[0]
                        opt_point = min_cost[1]
                        source_polarity = min_cost[2]
                        opt_destination_polarity = min_cost[3]
                        if (opt_point, opt_destination_polarity) not in past_path:
                            if  min_cost[0] == (14,30):
                                print(min_cost)
                            path = A_star_search(start, opt_point, source_polarity , opt_destination_polarity, crossbar)
                            crossbar = mapping_A_star(path, key, crossbar, opt_destination_polarity, source_polarity)
                        if path is not None: 
                            for i in range(len(path)):
                                if path[i] not in past_path:
                                    past_path.append(path[i])
                            break
                        if len(optimization_point_path) == 0:
                            no_path = True 
                            break

                    ## opt -> des ##
                    if len(optimization_point_path) == 0: continue
                    new_start = opt_point
                    idx_list = []

                    '''for idxx, ((rrrr, cccc), ddddes_polarity) in enumerate (source2):
                        if rrrr == opt_point[0] and cccc == opt_point[1]:
                            del source2[idxx]
                            break'''
                    source2 = [item for item in source2 if item[0] != opt_point]

                    coords_to_remove = set()
                    coords_to_remove = set()  # coordinates to remove in this step

                    for (rr, cc), des_polarity in source:
                        if (rr, cc) == opt_point:
                            continue
                        if (rr == opt_point[0] or cc == opt_point[1]) and (opt_destination_polarity != des_polarity):
                            if ((rr, cc), des_polarity) not in past_path:
                                path = A_star_search(opt_point, (rr, cc), opt_destination_polarity, des_polarity, crossbar)
                                if path:
                                    crossbar = mapping_A_star(path, key, crossbar, des_polarity, opt_destination_polarity)
                                    for step in path:
                                        if step not in past_path:
                                            past_path.append(step)
                                    new_start = (rr, cc)
                                else:
                                    # no path: skip this target
                                    continue

                            # mark for removal only the coordinates actually present in source2
                            if any((rr, cc) == (sr, sc) for ((sr, sc), _) in source2):
                                coords_to_remove.add((rr, cc))

                    # do not delete by index! remove all at once by filtering on coordinates
                    if coords_to_remove:
                        source2 = [item for item in source2 if item[0] not in coords_to_remove]

                    for idx in reversed(idx_list):
                        del source2[idx]

                    
                    idx_list = []
                    source3 = source2.copy()
                    while True:
                        retry = False
                        for idx, ((rr, cc), des_polarity) in enumerate (source2):
                            if retry == True: 
                                retry = False
                                break
                            for ((pr, pc), source_polarity) in past_path:
                                if rr == pr and cc == pc: continue
                                if rr == pr or cc == pc:
                                    if source_polarity != des_polarity:
                                        if ((rr, cc), des_polarity) not in past_path:
                                            path = A_star_search((pr, pc), (rr, cc), source_polarity, des_polarity, crossbar)
                                            crossbar = mapping_A_star(path, key, crossbar, des_polarity, source_polarity)
                                        else: break
                                        

                                        for i in range(len(path)):
                                            if path[i] not in past_path:
                                                past_path.append(path[i])
                                        
                                        #for idx2, ((sr, sc), polarity) in enumerate (source2):
                                        prev_len = len(source2)
                                        source2 = [item for item in source2 if item[0] != (rr, cc)]
                                        if len(source2) != prev_len:
                                            retry = True
                                            break
                                        break

                        if retry == False and idx >= len(source2)-1 or retry == True and len(source2) == 0: break

                    

                if len(source) == 1 or len(optimization_point_path) == 0:
                    no_path = False
                    if fanin[0] == "~":
                        destination_polarity = 0
                    else: destination_polarity = 1
                    destination = source[0][0]
                    start = sources[0][0] #IR position
                    start_polarity = sources[0][2]
                    path = A_star_search(start, destination, start_polarity, destination_polarity, crossbar)
                    if not path:
                        # The first source (usually the stored IR) can be enclosed (no free cell in its
                        # row or column); try the other cells that already hold this signal (earlier route copies),
                        # using routed points as additional sources (paper Sec. III-D).
                        for _alt in sources[1:]:
                            path = A_star_search(_alt[0], destination, _alt[2], destination_polarity, crossbar)
                            if path:
                                print(f"[A* routing] source {start} enclosed; routed from alternative source {_alt[0]}")
                                start, start_polarity = _alt[0], _alt[2]
                                break
                    
                    if path:
                        crossbar = mapping_A_star(path, key, crossbar, destination_polarity, start_polarity)
                        del source2[0]
                    else:
                        pass  # no crossbar print (a 256x256 print per level makes the log very large)
                        print('\n'+'\n')    
                        shortest_path = (0,0)
                        with open(_os.path.join(Path, "failure.txt"), "a") as f:
                            print("A*_search_Fail")
                            data ="A*_search_Fail: fanin = %s" % (fanin) #and lut = %s in level %d \n" % (fanin, cell_content, luts[cell_content].level)
                            f.write(data)
                            sys.exit()
                

        if last_key == key:
            return crossbar
                        
    return crossbar       

# ---- Routing method of Prev. [21] (ICCD'24): heuristic g, neighbour rule and A* ----
# Used only for the A* routing ablation (--routing astar, internally 'iccd').
def _iccd_g(r, c, Rd, Cd, start_polarity, destination_polarity, crossbar):
    if isinstance(destination_polarity, str):
        if (r == Rd or c == Cd):
            destination_polarity = 1-start_polarity
        elif (r != Rd and c != Cd):
            destination_polarity = start_polarity

    if (r, c) == (Rd, Cd):
        return 0, destination_polarity
    elif (r == Rd or c == Cd): #and (start_polarity != destination_polarity):
        return 1, destination_polarity
    else: return 2, destination_polarity
def _iccd_get_neighbors(r, c, Rd, Cd, crossbar, source_polarity, destination_polarity, source, A_star_idx):
    neighbors = []
    #col_neighbor = crossbar.grid[Rd][Cd].split("_")[1]
    #if col_neighbor[0] == "~":
     #   col_neighbor[1:]
    #if crossbar.grid[r][c] is not None:
     #   if "_" in crossbar.grid[r][c]:
      #      IR = crossbar.grid[r][c].split("_")[1]
       #     if IR[0] == "~":
        #        IR = IR[1:]

    if  c == Cd:       
        #if A_star_idx == 1 and (r,c) == source and "_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[1][-2:] == crossbar.grid[r][c].split("_")[0][-2:] or 
        if crossbar.grid[r][c] is None:
            if source_polarity != destination_polarity: #total path length = even
                if A_star_idx % 2 == 1:
                    neighbors.append((Rd,Cd))
                    return neighbors
                else: 
                    for i in range(len(crossbar.grid)):
                        if crossbar.grid[i][c] is None and i != r:
                            neighbors.append((i,Cd))
                            return neighbors
            else:
                if A_star_idx % 2 == 1:
                    for i in range(len(crossbar.grid)):
                        if crossbar.grid[i][c] is None and i != r:
                            neighbors.append((i,Cd))
                            return neighbors
                else: 
                    neighbors.append((Rd,Cd))
                    return neighbors
    if r == Rd:
        #if A_star_idx == 1 and (r,c) == source and "_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[1][-2:] ==  crossbar.grid[r][c].split("_")[0][-2:]or 
        if crossbar.grid[r][c] is None:
            if source_polarity != destination_polarity: #total path length = odd
                if A_star_idx % 2 == 1:
                    neighbors.append((Rd,Cd))
                    return neighbors
                else: 
                    for i in range(len(crossbar.grid[0])):
                        if crossbar.grid[r][i] is None and i != c:
                            neighbors.append((Rd,i))
                            return neighbors
            else:
                if A_star_idx % 2 == 1:
                    for i in range(len(crossbar.grid[0])):
                        if crossbar.grid[r][i] is None and i != c:
                            neighbors.append((Rd,i))
                            return neighbors
                else: 
                    neighbors.append((Rd,Cd))
                    return neighbors
                
    if crossbar.grid[r][Cd] is None:
        if source_polarity == destination_polarity: #total path length = odd
            if A_star_idx % 2 == 1:
                neighbors.append((r,Cd))
                return neighbors
            else: 
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[r][i] is None and i != c:
                        neighbors.append((r,i))
                        return neighbors
        else:
            if A_star_idx % 2 == 1:
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[r][i] is None and i != c:
                        neighbors.append((r,i))
                        return neighbors
            else: 
                neighbors.append((r,Cd))
                return neighbors
            
    if crossbar.grid[Rd][c] is None:
        if source_polarity == destination_polarity: #total path length = odd
            if A_star_idx % 2 == 1:
                neighbors.append((Rd,c))
                return neighbors
            else: 
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[i][c] is None and i != r:
                        neighbors.append((i,c))
                        return neighbors
        else:
            if A_star_idx % 2 == 1:
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[i][c] is None and i != r:
                        neighbors.append((i,c))
                        return neighbors
            else: 
                neighbors.append((Rd,c))
                return neighbors

    neighbors2 = []
    if A_star_idx % 2 == 1:
        for i in range(len(crossbar.grid[0])):
            if crossbar.grid[r][i] is None:
                if crossbar.grid[Rd][i] is None or crossbar.grid[r][Cd] is None:
                    neighbors.append((r, i))
                    neighbors2.append((r, i))
                else:
                    neighbors2.append((r, i))

        if len (neighbors) >= 10: 
            return neighbors

        for i in range(len(crossbar.grid)):
            if crossbar.grid[i][c] is None:
                neighbors2.append((i, c))
    else:
        for i in range(len(crossbar.grid)):
            if crossbar.grid[i][c] is None:

                if crossbar.grid[Rd][c] is None or crossbar.grid[i][Cd] is None:
                    neighbors.append((i, c))
                    neighbors2.append((i, c))
                else:
                    neighbors2.append((i, c))

        if len (neighbors) >= 10: 
            return neighbors

        for i in range(len(crossbar.grid[0])):
            if crossbar.grid[r][i] is None:
                neighbors2.append((r,i))

    return neighbors2
def _iccd_A_star_search(source, destination, source_polarity, destination_polarity, crossbar):
    """
    Optimized version:
      - state (r,c,pol) encoded as an int
      - neighbor bulk push + heapify to minimize heapq operations
      - heuristic fallback is an inline Manhattan distance
    """
    global place_info_dict
    Rs, Cs = source
    Rd, Cd = destination
    R, C = len(crossbar.grid), len(crossbar.grid[0])

    # -------- State encode/decode --------
    def encode_state(r, c, pol):
        return ((r * C) + c) << 1 | pol

    def decode_state(state_id):
        pol = state_id & 1
        idx = state_id >> 1
        r, c = divmod(idx, C)
        return r, c, pol

    # -------- Neighbor cache --------
    @lru_cache(maxsize=None)
    def cached_neighbors(r, c, path_rev):
        raw = _iccd_get_neighbors(r, c, Rd, Cd, crossbar,source_polarity, destination_polarity, source, path_rev)
        seen = set()
        cleaned = []
        for nr, nc in raw:
            if (nr, nc) == (r, c):
                continue
            if (nr, nc) not in seen:
                seen.add((nr, nc))
                cleaned.append((nr, nc))
        return tuple(cleaned)

    # -------- Heuristic --------
    if True:  # Prev.'s heuristic
        @lru_cache(maxsize=None)
        def heuristic(nr, nc, pol):
            h, _ = _iccd_g(nr, nc, Rd, Cd, pol, destination_polarity, crossbar)
            return h
    else:
        @lru_cache(maxsize=None)
        def heuristic(nr, nc, pol):
            return abs(nr - Rd) + abs(nc - Cd)

    # -------- Path reconstruction --------
    def reconstruct_path(came_from, end_state_id):
        path_rev = []
        cur = end_state_id
        while cur is not None:
            r, c, pol = decode_state(cur)
            path_rev.append(((r, c), pol))
            cur = came_from.get(cur, None)
        path_rev.reverse()
        return path_rev

    # -------- Init --------
    log_section_time("A_star_search::section1 init", time.time())
    open_heap = []
    start_id = encode_state(Rs, Cs, source_polarity)
    start_h = heuristic(Rs, Cs, source_polarity)
    heapq.heappush(open_heap, (start_h, 0, start_id))

    closed_set = set()
    came_from = {start_id: None}
    gscore = {start_id: 0}

    # -------- Search --------
    while open_heap:
        t0 = time.time()
        priority, gcur, state_id = heapq.heappop(open_heap)
        log_section_time("A_star_search::section2-1", t0)

        if state_id in closed_set:
            continue
        closed_set.add(state_id)

        r, c, pol = decode_state(state_id)

        # goal test
        if (r, c) == (Rd, Cd):
            same_pol = (source_polarity == destination_polarity)
            if (same_pol and (gcur % 2 == 0)) or ((not same_pol) and (gcur % 2 == 1)):
                path_rev = reconstruct_path(came_from, state_id)
                return path_rev
        #else: path_rev = []
        # expand neighbors (optimized: heappush only)
        t3 = time.time()
        path_len = gscore[state_id] + 1
        for nr, nc in cached_neighbors(r, c, path_len):
            new_pol = 1 - pol
            nstate = encode_state(nr, nc, new_pol)
            if nstate in closed_set:
                continue

            tentative_g = gcur + 1
            prev_g = gscore.get(nstate)
            if prev_g is None or tentative_g < prev_g:
                gscore[nstate] = tentative_g
                came_from[nstate] = state_id
                h = heuristic(nr, nc, new_pol)
                f = tentative_g + h
                heapq.heappush(open_heap, (f, tentative_g, nstate))
        log_section_time("A_star_search::section6", t3)


def _execute_copy_iccd(crossbar):
    """A* routing ablation (--routing astar, internally 'iccd'): Prev.'s routing method on the Ours core.
    Every fan-in cell of the level is routed on its own in row-major order (as Prev.'s find_fanin scan), from the
    source that gives the shortest Prev.-A* path among the stored IR(s) and the cells that already hold the net
    (earlier fan-in cells and route cells). If no source has an A* path, a complete search is used (as in the
    Prev. mapper); no path at all is a failure."""
    global place_info_dict
    jobs, held = [], {}
    for key, dests in place_info_dict.items():
        fanin = "~" + key if dests[0][1] == 0 and key[0] != "~" else key
        held[key] = [(s[0], s[2]) for s in find_sources(fanin, crossbar)]
        for dest, dpol in dests:
            jobs.append((dest, dpol, key, fanin))
    jobs.sort(key=lambda j: j[0])
    for dest, dpol, key, fanin in jobs:
        best = None
        for st, sp in held[key]:
            path = _iccd_A_star_search(st, dest, sp, dpol, crossbar)
            if path and (best is None or len(path) < len(best[0])):
                best = (path, sp)
        if best is None:
            for st, sp in held[key]:
                path = _exact_copy_route(st, dest, sp, dpol, crossbar)
                if path and (best is None or len(path) < len(best[0])):
                    best = (path, sp)
            print(f"[A* routing] Prev. A* found no path for {fanin} -> {dest}; complete search: "
                  f"{'found ' + str(len(best[0]) - 1) + ' copies' if best else 'no route exists'}")
        if best is None:
            print("A*_search_Fail")
            with open(_os.path.join(Path, "failure.txt"), "a") as f:
                f.write(f"A*_search_Fail: fanin = {fanin}\n")
            sys.exit()
        mapping_A_star(best[0], key, crossbar, dpol, best[1])
        for p_ in best[0]:
            if p_ not in held[key]:
                held[key].append(p_)
    return crossbar


def _rescue_route(fanin, destination, destination_polarity, crossbar, dest_cells, start, start_polarity):
    """Called only when no cell holding the net has a route to the fan-in cell (the run would stop with
    A*_search_Fail). Within a level, the copies left on the route cells of nets routed earlier are no longer read
    (their fan-in cells already hold the value), yet they occupy the empty cells a copy must pass through, and can
    enclose the stored IR (no empty cell in its row or column). Reset such dead copies one group at a time (one
    cycle each: the row, then the column of each cell holding the net, then of the fan-in cell, then every column)
    and route again after each reset. LUT cells of the level, stored IRs ('_' cells) and copies of this net are kept.
    Returns (path, start, start_polarity); path is None if no route exists even after all resets."""
    grid = crossbar.grid
    R, C = len(grid), len(grid[0])
    net = fanin.lstrip("~")

    def dead(r, c):
        v = grid[r][c]
        return v is not None and "_" not in v and (r, c) not in dest_cells and v.lstrip("~") != net

    def reset(cells, label):
        global Cycle, Reset_Cycle
        for r, c in cells:
            grid[r][c] = None
        with open(_os.path.join(Path, "instructions.txt"), "a") as f:
            f.write(f"C{Cycle} Reset Route ({label}): {', '.join(f'({r}, {c})' for r, c in cells)}\n")
        Cycle += 1
        Reset_Cycle += 1

    holders = [(s[0], s[2]) for s in find_sources(fanin, crossbar)]
    groups = []
    for (r, c), _ in holders + [(destination, destination_polarity)]:
        groups += [("r=%d" % r, [(r, j) for j in range(C)]), ("c=%d" % c, [(i, c) for i in range(R)])]
    groups += [("c=%d" % c, [(i, c) for i in range(R)]) for c in range(C)]
    n_resets = 0
    for label, cells in groups:
        cells = [rc for rc in cells if dead(*rc)]
        if not cells:
            continue
        reset(cells, label)
        n_resets += 1
        for (r, c), pol in holders:
            path = A_star_search((r, c), destination, pol, destination_polarity, crossbar)
            if path:
                print(f"[route rescue] {fanin} {(r, c)}->{destination} after {n_resets} reset cycle(s)")
                return path, (r, c), pol
    print(f"[route rescue] failed: {fanin} -> {destination} ({n_resets} reset cycle(s))")
    return None, start, start_polarity


def _route_each_fanin(key, fanin, dests, past_path, crossbar):
    """Conventional A* routing (internal routing mode 'astar'). Each fan-in cell of the net is routed on
    its own from the closest cell that already holds the net (the stored IR or an earlier route/fan-in of the
    same net), without the Hanan-grid shared points (paper Sec. III-D, 'previous approaches')."""
    for dest, dpol in dests:
        starts = sorted(past_path, key=lambda sp: g(sp[0][0], sp[0][1], dest[0], dest[1], sp[1], dpol, crossbar)[0])
        path, spol = None, None
        for st, sp in starts:
            path = A_star_search(st, dest, sp, dpol, crossbar)
            if path:
                spol = sp
                break
        if not path:
            print("A*_search_Fail")
            with open(_os.path.join(Path, "failure.txt"), "a") as f:
                f.write(f"A*_search_Fail: fanin = {fanin}\n")
            sys.exit()
        mapping_A_star(path, key, crossbar, dpol, spol)
        for p_ in path:
            if p_ not in past_path:
                past_path.append(p_)


@timer
def execute_Copy(luts, crossbar): 
    global place_info_dict
    if _args.routing == "iccd":  # A* routing ablation
        return _execute_copy_iccd(crossbar)
    place_info_dict_sorted = dict(sorted(place_info_dict.items(), key=lambda item: len(item[1]), reverse=True))
    _dest_cells = {pos for dests in place_info_dict.values() for pos, _ in dests}  # fan-in cells of this level
    fanin_list = list(place_info_dict_sorted.keys())

    row=0 
    col=0

    while (len(place_info_dict_sorted)):
        last_key = next(reversed(place_info_dict_sorted))
        for key,source in place_info_dict_sorted.items():  #sources : ((position), lut name, source_polarity)
            #if source in past_path: break
            if source[0][1] == 0 and key[0] != "~":
                fanin = "~" + key
            else: fanin = key

            past_path = []
            sources = find_sources(fanin, crossbar)
            past_path.append((sources[0][0],sources[0][2]))
            if _args.routing == "astar":  # conventional per-fan-in A*
                _route_each_fanin(key, fanin, source, past_path, crossbar)
                continue
            source2 = source.copy()
            optimization_point_path = []
            no_path = False
            
            while (len(source2) > 0):

                if len(source) > 1  and no_path == False:
                    if len(source2) == len(source): 
                        source2.append((sources[0][0],sources[0][2]))
                    optimization_point_list = small_crossbar(source2, crossbar)

                    if len(optimization_point_list) == 0:
                        optimization_point_list = small_crossbar(source2, crossbar)

                    if len(source2) == len(source) + 1:
                        del source2[-1]

                    new_optimization_point_list = cal_opt_cost(source2, optimization_point_list)
                    #(target coordinate, overlap count (sum), polarity, row/1, row/0, col/1, col/0)
                    
                    optimization_point_path = []
                    min_cost = 100
                    for s in new_optimization_point_list:
                        opt_destination_polarity = s[2]
                        Rd, Cd = s[0]
                        for (r,c), start_polarity in past_path:
                            h, n_opt_destination_polarity = g(r, c, Rd, Cd, start_polarity, opt_destination_polarity, crossbar) 
                            cost = h - s[1] #h = expected_cost, s[1] = overlap count (sum)
                            if min_cost >= cost:
                                min_cost = cost
                                optimization_point_path.append(((r, c), s[0], start_polarity, n_opt_destination_polarity, cost))
                                #start, opt_point, start_polarity, opt_destination_polarity, cost
                
                if len(source) != 1 and len(optimization_point_list) > 0 and no_path == False:
                    ## start -> opt ##
                    optimization_point_path.sort(key=lambda x: x[-1])
                    while True:
                        for idx, i in enumerate(optimization_point_path):
                            #if i[-1] == min_cost:
                                #if (i[0],i[2]) in source:
                                    min_cost = i
                                    del optimization_point_path[idx]
                                    break
                        if isinstance(min_cost, int): 
                            min_cost = min(optimization_point_path, key=lambda x: x[-1])
                        start = min_cost[0]
                        opt_point = min_cost[1]
                        source_polarity = min_cost[2]
                        opt_destination_polarity = min_cost[3]
                        if (opt_point, opt_destination_polarity) not in past_path:
                            if  min_cost[0] == (14,30):
                                print(min_cost)
                            path = A_star_search(start, opt_point, source_polarity , opt_destination_polarity, crossbar)
                            crossbar = mapping_A_star(path, key, crossbar, opt_destination_polarity, source_polarity)
                        if path is not None: 
                            for i in range(len(path)):
                                if path[i] not in past_path:
                                    past_path.append(path[i])
                            break
                        if len(optimization_point_path) == 0:
                            no_path = True 
                            break

                    ## opt -> des ##
                    if len(optimization_point_path) == 0: continue
                    new_start = opt_point
                    idx_list = []

                    for idxx, ((rrrr, cccc), ddddes_polarity) in enumerate (source2):
                        if rrrr == opt_point[0] and cccc == opt_point[1]:
                            del source2[idxx]
                            break 


                    for (rr, cc), des_polarity in source:
                        if rr == opt_point[0] and cc == opt_point[1]: 
                            continue
                        if rr == opt_point[0] or cc == opt_point[1]:
                            if opt_destination_polarity != des_polarity:
                                if ((rr, cc), des_polarity) not in past_path:
                                    path = A_star_search(opt_point, (rr, cc), opt_destination_polarity, des_polarity, crossbar)
                                    crossbar = mapping_A_star(path, key, crossbar, des_polarity, opt_destination_polarity)

                                for i in range(len(path)):
                                    if path[i] not in past_path:
                                        past_path.append(path[i])
                                new_start = (rr, cc)

                                for idx, ((sr, sc), polarity) in enumerate (source2):
                                    if (rr, cc) == (sr, sc):
                                        idx_list.append(idx)
                                        break

                    for idx in reversed(idx_list):
                        del source2[idx]

                    
                    idx_list = []
                    source3 = source2.copy()
                    while True:
                        retry = False
                        for idx, ((rr, cc), des_polarity) in enumerate (source2):
                            if retry == True: 
                                retry = False
                                break
                            for ((pr, pc), source_polarity) in past_path:
                                if rr == pr and cc == pc: continue
                                if rr == pr or cc == pc:
                                    if source_polarity != des_polarity:
                                        if ((rr, cc), des_polarity) not in past_path:
                                            path = A_star_search((pr, pc), (rr, cc), source_polarity, des_polarity, crossbar)
                                            crossbar = mapping_A_star(path, key, crossbar, des_polarity, source_polarity)
                                        else: break
                                        

                                        for i in range(len(path)):
                                            if path[i] not in past_path:
                                                past_path.append(path[i])
                                        
                                        for idx2, ((sr, sc), polarity) in enumerate (source2):
                                            if (rr, cc) == (sr, sc):
                                                del source2[idx2]
                                                retry = True
                                                break
                                        break

                        if retry == False and idx >= len(source2)-1 or retry == True and len(source2) == 0: break

                    

                if len(source) == 1 or len(optimization_point_path) == 0:
                    no_path = False
                    if fanin[0] == "~":
                        destination_polarity = 0
                    else: destination_polarity = 1
                    destination = source[0][0]
                    start = sources[0][0] #IR position
                    start_polarity = sources[0][2]
                    path = A_star_search(start, destination, start_polarity, destination_polarity, crossbar)
                    if not path:
                        # The first source (usually the stored IR) can be enclosed (no free cell in its
                        # row or column); try the other cells that already hold this signal (earlier route copies),
                        # using routed points as additional sources (paper Sec. III-D).
                        for _alt in sources[1:]:
                            path = A_star_search(_alt[0], destination, _alt[2], destination_polarity, crossbar)
                            if path:
                                print(f"[A* routing] source {start} enclosed; routed from alternative source {_alt[0]}")
                                start, start_polarity = _alt[0], _alt[2]
                                break
                    if not path:  # free dead route copies, then route again (only when routing would otherwise fail)
                        path, start, start_polarity = _rescue_route(fanin, destination, destination_polarity, crossbar,
                                                                    _dest_cells, start, start_polarity)
                    
                    if path:
                        crossbar = mapping_A_star(path, key, crossbar, destination_polarity, start_polarity)
                        del source2[0]
                    else:
                        pass  # no crossbar print (a 256x256 print per level makes the log very large)
                        print('\n'+'\n')    
                        shortest_path = (0,0)
                        with open(_os.path.join(Path, "failure.txt"), "a") as f:
                            print("A*_search_Fail")
                            data ="A*_search_Fail: fanin = %s" % (fanin) #and lut = %s in level %d \n" % (fanin, cell_content, luts[cell_content].level)
                            f.write(data)
                            sys.exit()
                

        if last_key == key:
            return crossbar
                        
    return crossbar
    
    

def init_SET(mapping_info):     #takes the initial LUTs and builds the SET
    global lut_max_V
    H = [2, 3, 4, 5]
    sizes = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]

    SET_Sequence = []   #list of the sizes to be mapped now, out of all sizes
    
    for i in range(len(mapping_info)):
        for j in range(4):
            if mapping_info[i][j] != 0:
                size = sizes[i][j]
                if size not in SET_Sequence:
                    SET_Sequence.append(size)
    return SET_Sequence

def compute_count_size(mapping_info, SET_Sequence):
    global lut_max_V
    H = [2, 3, 4, 5]
    keys_size = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]
    
    count_size={}  

    for i in range(len(mapping_info)):
        for j in range(4):
            if mapping_info[i][j] != 0:
                if keys_size[i][j] in SET_Sequence:
                    count_size[keys_size[i][j]] = mapping_info[i][j]        
    
    return count_size



def _usable_width(crossbar):
    """The LUT region must stay left of the rightmost fully empty column (Folding_This's last_c),
    i.e. the blockage of the IR region (paper Fig. 6). Returns that column index; 0 if none is empty."""
    for c in range(len(crossbar.grid[0]) - 1, -1, -1):
        if all(row[c] is None for row in crossbar.grid):
            return c
    return 0


def Folding_This(sorted_V_list, crossbar, count_size, area_VH):
    global lut_max_V
    new_sorted_V_list = []
    current_V2 = 0
    current_H2 = 0
    V_difference = [] 
    count_size2 = count_size.copy() 
    last_c=0

    for c in range(len(crossbar.grid[0]) - 1, -1, -1):
        if all(cell is None for cell in [row[c] for row in crossbar.grid]):
            last_c = c
            break

    for n in range(4):
        current_H2 += int(area_VH["H" + str(n+2)])
    for nn in range(lut_max_V-1):
        current_V2 += int(area_VH["V" + str(nn+2)])  

    if current_V2 <= len(crossbar.grid) and current_H2 < last_c:
        # check whether all counts are below 5
        if all(count < 5 for count in count_size2.values()):
            return area_VH, None

    new_temp_V = cal_V_temp(count_size,area_VH)
    sorted_V_difference = compute_reduce_space(V_difference, new_temp_V,area_VH)
    
    while True:
        for (temp_V, value) in sorted_V_list:
            #if sorted_V_difference[0][0] == temp_V: # if sorted_V_difference[0][0] != temp_V it is not the largest difference, so pass
                for i in range (len(new_temp_V)):
                    if new_temp_V[i][0] == temp_V:
                        V_list = sorted(new_temp_V[i][1], reverse=True)
                        break

                folding_H = 0 
                for kkkey,cccount in count_size2.items():
                    if int(kkkey[1]) * int(cccount) == value:
                        folding_H = int(kkkey[0]) #H of the size being folded

                real_folding_H = 0
                for kkkkey,ccccount in area_VH.items():
                    if kkkkey == "H"+str(folding_H):
                        real_folding_H = ccccount #H accounting for folding


                if len(sorted_V_difference) == 1:
                    if (int (value) % len(crossbar.grid)) == 0:
                        k = int(int (value) // len(crossbar.grid)) # k: how many times to fold, e.g. 2 means placing in two parts
                    else: 
                        k = int(int (value) // len(crossbar.grid)) +1
                else:
                    if len(sorted_V_difference) != 1: 
                        k = int(real_folding_H / folding_H) if folding_H else 0  # folding_H is 0 when no single LUT size fills the V group (k then falls back to 2)
                        k+=1 #with several V groups, increase the folding factor by one

                if k == 1: k+=1
                if k == 0: k+=2
                
                for j in range (len(sorted_V_list)):
                    if sorted_V_list[j][0] == temp_V:
                        V_max_i = int(sorted_V_list[j][1])
                        break
                
                i = -1
                current_V3 = 0
                for n in range(lut_max_V-1):
                    if (n+2) != int(temp_V[1]):
                        current_V3 += int(area_VH["V" + str(n+2)]) 

                if real_folding_H != folding_H: #already folded
                    while True: 
                    #compute the maximum V (height) after folding
                        if (V_max_i % k) != 0: V_max_div = (V_max_i*(k-1) // k) + 1
                        else: V_max_div = V_max_i*(k-1) // k

                        if (V_max_div) % (int(temp_V[1])) == 0:
                            V_max = V_max_div
                        else:
                            V_max = (V_max_div) + (int(temp_V[1]) - (V_max_div) % (int(temp_V[1]))) 

                        #2. whether it improves cycles over the previous one
                        if k + V_max > k-1 + V_max_i: return area_VH,None

                        #check whether the height after folding fits in the grid
                        if current_V3 + V_max > int(len(crossbar.grid)):
                            k+=1
                            i+=1
                            if i == 1 and len(sorted_V_difference) == 1: return None, None
                        else: break

                        #check whether the width after folding fits in the grid
                        only_H_list = []
                        sum_H = 0
                        for kkey,ccount in area_VH.items():
                            if kkey[0] == "H" and int(ccount) != 0:
                                only_H_list.append((kkey,ccount))

                        for i in range(len(only_H_list)):
                            sum_H += int(only_H_list[i][1]) 
                            
                        if len(sorted_V_difference) == 1:
                            if sum_H + folding_H*(k-1) < int(last_c):
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                        else:
                            if sum_H + folding_H < int(last_c): #fold only while the IR limit is not exceeded
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                else:  #not folded
                    while True: 
                    #compute the maximum V (height) after folding
                        if (V_max_i % k) != 0: V_max_div = (V_max_i // k) + 1
                        else: V_max_div = V_max_i // k

                        if (V_max_div) % (int(temp_V[1])) == 0:
                            V_max = V_max_div
                        else:
                            V_max = V_max_div + (int(temp_V[1]) - (V_max_div) % (int(temp_V[1]))) 

                        #2. whether it improves cycles over the previous one
                        if k + V_max > k-1 + V_max_i: return area_VH, None

                        #check whether the height after folding fits in the grid
                        if current_V3 + V_max > int(len(crossbar.grid)):
                            k+=1
                            i+=1
                            if i == 1 and len(sorted_V_difference) == 1: return None,None
                        else: break

                        #check whether the width after folding fits in the grid
                        only_H_list = []
                        sum_H = 0
                        for kkey,ccount in area_VH.items():
                            if kkey[0] == "H" and int(ccount) != 0:
                                only_H_list.append((kkey,ccount))

                        for i in range(len(only_H_list)):
                            sum_H += int(only_H_list[i][1]) 
                            
                        if len(sorted_V_difference) == 1:
                            if sum_H + folding_H*(k-1) < int(last_c):
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                        else:
                            if sum_H + folding_H < int(last_c): #fold only while the IR limit is not exceeded
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None

                #check whether the width after folding fits in the grid
                only_H_list = []
                sum_H = 0
                for kkey,ccount in area_VH.items():
                    if kkey[0] == "H" and int(ccount) != 0:
                        only_H_list.append((kkey,ccount))

                for i in range(len(only_H_list)):
                    sum_H += int(only_H_list[i][1]) 
                    
                if len(sorted_V_difference) == 1:
                    if sum_H + folding_H*(k-1) < int(last_c):
                        area_VH["H"+str(folding_H)] = folding_H*k
                    else: return None, None
                else:
                    if sum_H + folding_H < int(last_c): #fold only while the IR limit is not exceeded
                        area_VH["H"+str(folding_H)] = folding_H*k
                    else: return None, None
               
                # also consider the second largest V after folding
                if len(V_list) > 1: #two or more H
                    if V_max < int(V_list[1]):
                        area_VH[temp_V] = int(V_list[1]) #######difference
                    else:
                        #sorted_V_list[0] = (sorted_V_list[0][0], V_max)
                        area_VH[temp_V] = V_max
                else:
                    #sorted_V_list[0] = (sorted_V_list[0][0], V_max)
                    area_VH[temp_V] = V_max

                
                for size2, cnt in count_size.items():
                    #area_VH_value = int(area_VH["V"+str(size2[1])])
                    if folding_H == size2[0]:
                        if cnt != 1 :
                            if cnt % k == 0:
                                count_size2[size2] = int(cnt // k)
                              #  if area_VH_value > int(cnt // k) * int(size2[1]):
                               #     area_VH_value = int(cnt // k) * int(size2[1])
                            else:
                                count_size2[size2] = int(cnt // k) + 1
                                #if area_VH_value > int((cnt // k)+1) * int(size2[1]):
                                 #   area_VH_value = int((cnt // k)+1) * int(size2[1])
                                 
                H = [2, 3, 4, 5]

                keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # vertical values
                keys2 = [f"H{h}" for h in H]                    # horizontal values

                #count_VH = {key: 0 for key in keys}

                for idx, area_H in enumerate(keys1):    #idx==0 -> compute H2
                    temp_V = [0 for key in keys2 if area_VH.get(key, 0) != 0] ##len =1,2,3,4
                    for size, count in count_size2.items():
                        found = False
                        if(int(size[1]) == idx + 2):    
                            for i in range(len(temp_V)):
                                if temp_V[i] == 0 and not found:
                                    temp_V[i] = int(count)
                                    found = True
                                    break
                    
                    if temp_V and int(max(temp_V)) != 0:
                        for i in range(len(temp_V)):
                            temp_V[i] = int(temp_V[i]) * int(area_H[1])             
                        area_VH[area_H] = int(max(temp_V)) #actual occupied size, considering the V size

                print(count_size2)
                print(area_VH)
                     
                current_V = 0
                current_H = 0

                for n in range(4):
                    current_H += int(area_VH["H" + str(n+2)])
                for nn in range(lut_max_V-1):
                    current_V += int(area_VH["V" + str(nn+2)]) 

                    
                if len(only_H_list) == 1: 
                    if current_V <= len(crossbar.grid) and current_H < last_c:
                        return area_VH, count_size2

                new_temp_V = cal_V_temp(count_size2,area_VH)
                sorted_V_difference = compute_reduce_space(V_difference, new_temp_V,area_VH)

                #print(area_VH)

                ##########debugging
                V_dict_temp={}
                #if  current_V > len(crossbar.grid) and current_H < int(last_c): ##fold again
                for key, value in area_VH.items():
                    if key[0] == "V":
                        V_dict_temp[key] = value
                new_sorted_V_list = sorted(V_dict_temp.items(), key=lambda item: int(item[1]),reverse=True)
                #else: return None 

                #2. may it be folded further?
                if new_sorted_V_list[0][1] + current_H > last_c:
                    return area_VH, count_size2
                    
                if new_sorted_V_list != sorted_V_list:
                    sorted_V_list = new_sorted_V_list
                    break




def define_dict(count_size, crossbar):    #builds the area_VH dictionary: area occupied per horizontal/vertical size  #fold =0 no fold, fold==2 horizontal fold, fold ==3 vertical fold
    global lut_max_V
    #count_size={}       #number of LUTs per size to be mapped now, out of all 16 sizes
    #count_VH={}         #how many of horizontal 2,3,4,5 / vertical 2,3,4,5 exist, initial value = 0
    area_VH={}          #rows/cols occupied by each of the 8 sizes horizontal 2,3,4,5 / vertical 2,3,4,5 (not index)
    V_difference = []
    temp_global = 0      #compare the area of the whole crossbar with the current set  [horizontal, vertical]
    sorted_temp_V = []
    sorted_V_difference = []

    H = [2, 3, 4, 5]
    keys_size = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]

    keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # vertical values
    keys2 = [f"H{h}" for h in H]                    # horizontal values
    #count_VH = {key: 0 for key in keys}
    area_VH = {key: 0 for key in keys2}
    area_VH.update({key: 0 for key in keys1})

    for size in count_size.keys():
        temp_global += int(size[1]) * int(size[0])
        area_VH["H" + str(size[0])] = int(size[0])
    for idx, area_H in enumerate(keys1):    #idx==0 -> compute H2
        temp_V = [0 for key in keys2 if area_VH.get(key, 0) != 0] ##len =1,2,3,4
        for size, count in count_size.items():
            found = False
            if(int(size[1]) == idx + 2):    
                for i in range(len(temp_V)):
                    if temp_V[i] == 0 and not found:
                        temp_V[i] = int(count)
                        found = True
                        break
        
        if temp_V and int(max(temp_V)) != 0:
            for i in range(len(temp_V)):
                temp_V[i] = int(temp_V[i]) * int(area_H[1])             
            area_VH[area_H] = int(max(temp_V)) #actual occupied size, considering the V size

            

    
    #if temp_global >= len(crossbar.grid[0])*len(crossbar.grid[1]) / 2 :  #run if the current LUT area is larger than half of the crossbar
     #   This_VH =[0,0]
      #  for key, value in area_VH.items():
       #     if This_VH[1] < value:
        #        This_VH = key, value 

        #return {0: This_VH[0]}      # Dictionary 0: which one to remove, e.g. V3
    
    return area_VH 
    #return count_size
    #return temp_global

@timer  
def cal_V_temp(count_size,area_VH):
    global lut_max_V
    new_temp_V = []
    H = [2, 3, 4, 5]

    keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # vertical values
    keys2 = [f"H{h}" for h in H]                    # horizontal values

    print(keys1)
    print(keys2)

    #count_VH = {key: 0 for key in keys}

    used_H=[]

    for HH in keys2: 
        if area_VH.get(HH, 0) != 0:
            used_H.append(HH)
            
    for idx, area_H in enumerate(keys1):    #idx==0 -> compute H2
        temp_V = [0 for key in keys2 if area_VH.get(key, 0) != 0] ##len =1,2,3,4
        for size, count in count_size.items():
            found = False
            if(int(size[1]) == idx + 2):    
                for i in range(len(temp_V)):
                    if not found:
                        for idx2, H_idx in enumerate(used_H):
                            if (int(size[0]) == int(H_idx[1])):
                                temp_V[idx2] = int(count)
                                found = True
                                break
        
        if temp_V and int(max(temp_V)) != 0:
            for i in range(len(temp_V)):
                temp_V[i] = int(temp_V[i]) * int(area_H[1])             
            area_VH[area_H] = int(max(temp_V)) 
            new_temp_V.append((area_H,temp_V))
    
    temp = []
    for i in range(2, lut_max_V + 1):
        row_size = int(0)
        for size2,count2 in count_size.items():
            if size2[1] == i:
                if count2 > row_size:
                    row_size = count2
                    temp = size2
        
        for HV,count3 in area_VH.items():
            if HV[0] == "H" and int (count3) != int(0):
                col_size = int(count3)

        done = False
        for j in range (len(new_temp_V)):
            if done == True: break
            if int(new_temp_V[j][0][1]) == int(i):
                for HV,count3 in area_VH.items():
                    if HV[0] == "H" and int (count3) != int(0) and int(HV[1]) == int(temp[0]):
                        temp = new_temp_V[j] + (count3,)
                        new_temp_V.append(temp)
                        del new_temp_V[0]
                        done = True
                        break
        temp = []

    return new_temp_V

@timer    
def compute_reduce_space(V_difference, new_temp_V,area_VH):
    V_difference = []
    i = 0

    if len(new_temp_V[0][1]) == 1 and (len(new_temp_V) != 1):
        for label, values, max_H in new_temp_V:
            if values[0] > i:
                i = values[0]

        return [[label, i]]


    for label, values, max_H in new_temp_V:
        sorted_temp_V = sorted(values, reverse=True)

        if (len(sorted_temp_V) == 1):
                return [[label, sorted_temp_V[0]]]
                

        if sorted_temp_V[0] % 2 == 0:
            if sorted_temp_V and int((sorted_temp_V[0]//2)) >= int(sorted_temp_V[1]):
                V_difference.append((label,int(sorted_temp_V[0]//2)))
            else:
                V_difference.append((label,int(sorted_temp_V[0] - sorted_temp_V[1])))
        else:
            if sorted_temp_V and int((sorted_temp_V[0]//2)+1) >= int(sorted_temp_V[1]):
                V_difference.append((label,int(sorted_temp_V[0]//2)+1))
            else:
                V_difference.append((label,int(sorted_temp_V[0] - sorted_temp_V[1])))


    sorted_V_difference = sorted(V_difference, key=lambda x: x[1], reverse=True)
    return sorted_V_difference

def _repair_H(area_VH, count_size):
    """Widen each H band so that every LUT size class (h, v) fits in its band region
    (V{v} // v tile rows x H{h} // h tile columns, the candidate grid of the placement ILP). Folding_This
    can shrink a V band shared by several classes for one class without widening the others, which
    leaves the placement ILP infeasible. An area
    that already holds every class is returned unchanged."""
    a = dict(area_VH)
    for (h, v), n in count_size.items():
        rows_t = int(a.get(f"V{v}", 0)) // v
        if n > 0 and rows_t > 0:
            need = -(-n // rows_t) * h
            if need > int(a.get(f"H{h}", 0)):
                a[f"H{h}"] = need
    return a


def case_operate(SET_Sequence, crossbar, mapping_info, area_VH):
    global lut_max_V

    result_Sequence = SET_Sequence             #final Sequence as a 2-D list
    result_Sequence = [result_Sequence]
    #This_Sequence = SET_Sequence                #Seq used recursively
    case = 0
    extend_cnt = 0                      #number of SET extensions (number of resets)
    check_idx = 0      
    idx = 0                 #checked index

    _case_iters = 0
    while case<5:
        current_V = 0
        current_H = 0
        _case_iters += 1

        if idx == 0:
            count_size = {}
            count_size = compute_count_size(mapping_info, result_Sequence[check_idx])
            init_VH = {}
            V_difference = [] 
            count_size2 = count_size.copy()

        if idx == 1: 
            idx +=1

        if 0 not in area_VH:         #not 0 -> folding case
            V_dict_temp={}          #dictionary of the vertical sizes
            for key, value in area_VH.items():
                if key[0] == "V":
                    V_dict_temp[key] = value
            sorted_V_list = sorted(V_dict_temp.items(), key=lambda item: int(item[1]),reverse=True)     
                            ###list of the vertical sizes sorted in descending order (stored as tuples)
            
            H_dict_temp={}          #dictionary of the horizontal sizes
            for key, value in area_VH.items():
                if key[0] == "H":
                    H_dict_temp[key] = value
            sorted_H_list = sorted(H_dict_temp.items(), key=lambda item: int(item[1]),reverse=True)  

            new_temp_V = cal_V_temp(count_size2,area_VH)
            sorted_V_difference = compute_reduce_space(V_difference, new_temp_V,area_VH)
            
            _area_before = dict(area_VH)
            _fold = Folding_This(sorted_V_list, crossbar, count_size2, area_VH)
            area_VH = _fold[0]
            if _fold[1] is not None:  # keep the previous counts when folding returns none
                count_size2 = _fold[1]
            # No folding progress while the area still does not fit -> treat as a failed fold so
            # that the caller partitions the level (paper Sec. III-B) instead of looping / crashing
            if area_VH is not None:
                _fit_V = sum(int(area_VH["V" + str(nn+2)]) for nn in range(lut_max_V-1))
                _fit_H = sum(int(_repair_H(area_VH, count_size)["H" + str(n+2)]) for n in range(4))  # fit test on the widened area
                if not (_fit_V <= len(crossbar.grid) and _fit_H < int(len(crossbar.grid[0]))) and \
                        (area_VH == _area_before or _case_iters > 50):
                    print(f"[case_operate] folding made no progress (iter {_case_iters}) -> partition level")
                    area_VH = None

            if area_VH is None:
                if len(sorted_V_list) <= 1 or sorted_V_list[1][1] == 0: #only one V left
                    if len(sorted_H_list) <= 1 or sorted_H_list[1][1] == 0:
                        for key in V_dict_temp:
                            init_VH[key] = V_dict_temp[key]
                        for key in H_dict_temp:
                            init_VH[key] = H_dict_temp[key]
                        return None, init_VH
                    else:
                        return None, sorted(sorted_H_list[0][0])
                else:
                    return None, sorted(sorted_V_difference[0][0])
            
            _rep = _repair_H(area_VH, count_size)  # fit test on the widened area
            for n in range(4):
                current_H += int(_rep["H" + str(n+2)])
            for nn in range(lut_max_V-1):
                current_V += int(area_VH["V" + str(nn+2)]) 
                 
            # Fit test against the full crossbar width
            if current_V <= len(crossbar.grid) and current_H < int(len(crossbar.grid[0])):  ##both within the crossbar size
                area_VH = _rep  # use the widened area
                case = 7
                continue
            idx += 1
            
    return area_VH


@timer
def Blocking(mapping_info, crossbar):
    seq = init_SET(mapping_info)
    count_size = compute_count_size(mapping_info, seq)
    print (count_size)
    init_area_VH = define_dict( count_size, crossbar)
    result_area_VH = case_operate(seq, crossbar, mapping_info, init_area_VH)

    if None in result_area_VH:
        return result_area_VH[0], result_area_VH[1]
    return result_area_VH

def execute_NOR(current_level, luts, crossbar):
    global Attempt
    global Cycle
    global Path
    global VNOR_Cycle
    global HNOR_Cycle
    global current_level_luts2

    past_row_cells = []
    past_col_cells = []

    file_path = os.path.join(Path, "instructions.txt")

    rows = len(crossbar.grid)
    cols = len(crossbar.grid[0])

    for current_level_lut in current_level_luts2:
        row_cells = []
        col_cells = []
        past_row_cells2 = []
        past_col_cells2 = []
    # ---- HNOR (row-wise NOR) ----
        Hdone = False
        for r in range(rows):
            # collect the current-level LUT cells in this row
            if Hdone == True: break
            for c in range(cols):
                a_star = crossbar.grid[r][c]
                if crossbar.grid[r][c] is not None and "_" not in crossbar.grid[r][c] and crossbar.grid[r][c][0] == "~": 
                    a_star = crossbar.grid[r][c][1:]
                
                if crossbar.grid[r][c] is not None and ("_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[0] == current_level_lut.name):
                    if c not in past_row_cells:
                        if c not in row_cells:
                            row_cells.append(c)
                
                
                if crossbar.grid[r][c] is not None and  "_" not in crossbar.grid[r][c]:
                    for rr in current_level_lut.row: 
                        if rr == r:
                            for cc in current_level_lut.col:
                                if cc == c:
                                    for cfanin in current_level_lut.fanin:
                                        if a_star == cfanin:
                                            if c not in past_row_cells:
                                                if c not in row_cells:
                                                    row_cells.append(c)


                if current_level_lut.size[0] == len(row_cells) and past_row_cells2 != row_cells:  # HNOR can run (2 or more cells)
                    with open(file_path, 'a', encoding='utf-8') as f:
                        row_cells.sort()
                        data = f"C{Cycle} HNOR col:{row_cells[:-1]} -> {row_cells[-1]}\n"
                        f.write(data)
                    Cycle += 1
                    HNOR_Cycle += 1
                    for rr in row_cells:
                        past_row_cells.append(rr) 
                        past_row_cells2.append(rr) 
                    Hdone = True

    # Two phases: every HNOR of the level is issued first, then every VNOR. A VNOR acts on all
    # columns of its rows, so it may stand for several LUTs with the same rows (the dedup below); issuing
    # all HNORs first ensures that no VNOR evaluates a LUT before that LUT's HNOR has run.
    for current_level_lut in current_level_luts2:
        col_cells = []
        past_col_cells2 = []
        # ---- VNOR (column-wise NOR) ---- and "_" in crossbar.grid[r][c]
        Vdone = False
        for c in range(cols):
            if Vdone == True: break
            for r in range(rows):
                if crossbar.grid[r][c] is not None and crossbar.grid[r][c].split("_")[0] == current_level_lut.name:
                    if r not in past_col_cells:
                        if r not in col_cells:
                            col_cells.append(r)

                if current_level_lut.size[1] == len(col_cells) and past_col_cells2 != col_cells:  # VNOR can run (2 or more cells)
                    with open(file_path, 'a', encoding='utf-8') as f:
                        col_cells.sort()
                        data = f"C{Cycle} VNOR  col:{c}, row:{col_cells[:-1]} -> {col_cells[-1]}\n"
                        f.write(data)
                    Cycle += 1
                    VNOR_Cycle += 1
                    for rr in col_cells:
                        past_col_cells.append(rr) 
                        past_col_cells2.append(rr) 
                    Vdone = True

    return
"""
def original_luts(luts, current_level,lut_level_up):
    ##LUTs before level up
    original_this_luts = {}
    if lut_level_up == 0:
        for lut in luts:
            if luts[lut].level == current_level:
                    original_this_luts[lut] = luts[lut]
"""

@timer
def max_luts_in_one_V(max_v,V_num,H_num,crossbar):
    global lut_max_V
    max_luts=[]
    for c in range(len(crossbar.grid[0]) - 1, -1, -1):
        if all(cell is None for cell in [row[c] for row in crossbar.grid]):
            last_c = int(c)
        else: last_c = int(0.75*len(crossbar.grid[0]))

    for i in range (2,lut_max_V+1):
        if i == V_num:
            max_H = int(last_c / H_num)
            max_V = int(len(crossbar.grid) / i)
            max_luts = (i,int(max_H * max_V))

    for j in range(lut_max_V):
        if max_luts[j][0] == V_num:
            if max_v % max_luts[j][1] == 0:
                max_luts_num = max_v / max_luts[j][1]
            else: 
                max_luts_num = (max_v / max_luts[j][1]) + 1 
    return max_luts_num


@timer
def levelup_luts(luts, current_level, area_VH, lut_level_up, crossbar, ordered_luts):

    if isinstance(area_VH[1], list):
        this_luts = {}
        past_luts = {}
        num = int(area_VH[1][0])
        if area_VH[1][1] == "V":
            for lut in luts:
                if luts[lut].level == current_level:
                    if luts[lut].size[1] == int(num):
                        luts[lut].level += 1
                    this_luts[lut] = luts[lut]

                elif luts[lut].level > current_level:
                    if lut_level_up == 0:
                        luts[lut].level += 1
                    this_luts[lut] = luts[lut]

                elif luts[lut].level < current_level: 
                    past_luts[lut] = luts[lut]
        else:
            for lut in luts:
                if luts[lut].level == current_level:
                    if luts[lut].size[0] == int(num):
                        luts[lut].level += 1
                        this_luts[lut] = luts[lut]
                    else: this_luts[lut] = luts[lut]

                elif luts[lut].level > current_level:
                    if lut_level_up == 0:
                        luts[lut].level += 1
                    this_luts[lut] = luts[lut]

                elif luts[lut].level < current_level: 
                    past_luts[lut] = luts[lut]

        for idx, lut in enumerate(this_luts):
            for chek_lut in luts:
                if lut in luts[chek_lut].fanin:                
                    luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)

        for idx, lut in enumerate(past_luts):
            for chek_lut in luts:
                if lut in luts[chek_lut].fanin:                
                    luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)

        

        final_luts = sorted(this_luts.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
        return final_luts
    
    elif isinstance(area_VH[1], dict):
        v_results = []
        this_level_luts = {}
        this_luts = {}
        idx = 0

        for k, v in area_VH[1].items(): ##level-up logic when only one V is left
            if k.startswith('V') and v != 0:
                V_num = int(k[1:]) # 'V3' → 3
                max_v = v
            if k.startswith('H') and v != 0:
                H_num = int(k[1:])

        result = max_v / V_num
        #max_luts_num = max_luts_in_one_V(max_v,V_num,H_num,crossbar)
        if result % 2 == 0:
            result = result / 2
            v_results.append(int(result))
            v_results.append(int(result))
        else:
            result = result // 2
            v_results.append(int(result))
            v_results.append(int(result+1))


        one_V_luts = {}
        one_V_luts_sorted = {}
        for lut in luts:
            if luts[lut].level == current_level:
                one_V_luts[lut] = luts[lut]
            elif luts[lut].level > current_level:
                luts[lut].level += 1
                this_level_luts[lut] = luts[lut]

        #one_V_luts_sorted = dict(sorted(one_V_luts.items(), key=lambda item: item[1].fanlevel))
        from collections import Counter

        # 1. count the occurrences of each net (input)
        fanin_element_counts = Counter(
            net
            for obj in one_V_luts.values()
            for net in obj.fanin
        )

        # 2. LUT score: sum of the occurrence counts of all input nets of the LUT
        def lut_score(obj):
            return sum(fanin_element_counts[net] for net in obj.fanin)

        # 3. sort by score descending (ties: more fanins first)
        one_V_luts_sorted = dict(
            sorted(
                one_V_luts.items(),
                key=lambda kv: (lut_score(kv[1]), len(kv[1].fanin)),
                reverse=True
            )
        )



        for lut in one_V_luts_sorted:
            if idx == v_results[0]:
                one_V_luts_sorted[lut].level += 1
                this_level_luts[lut] = one_V_luts_sorted[lut]
            else: 
                this_level_luts[lut] = one_V_luts_sorted[lut]
                idx +=1

        for idx, lut in enumerate(luts):
            for chek_lut in luts:
                if lut in luts[chek_lut].fanin:                
                    luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)

        merged = {**this_level_luts, **this_luts}
        final_luts = sorted(merged.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
        return final_luts


@timer
def update_mapping_info(mapping_info, current_level, area_VH, edit, lut_level_up):
    global lut_max_V

    if lut_level_up == 1:
        insert_info = [[0 for _ in range(4)] for n in range(lut_max_V)]
    else: insert_info = mapping_info[current_level]
    
    num = int(area_VH[1][0])
    #if area_VH[1][1] == "V" and int(num) > lut_max_V :return False

    if edit == 1:
        val = ()
        if current_level!=1:
            for i in range(4):
                if mapping_info[current_level-1][int(num)-2][i] !=0:
                    value = int(mapping_info[current_level-1][int(num)-2][i])
                    if value % 2 == 0:
                        val = (int(value/2),int(value/2))
                        insert_info = [[0 for _ in range(4)] for n in range(lut_max_V)]
                        mapping_info[current_level-1][int(num)-2][i] = val[0]
                        mapping_info.insert(current_level,insert_info)
                        mapping_info[current_level][int(num)-2][i] = val[1]
                        return mapping_info
                    else:
                        val = (int(value/2),int(value/2 +1))
                        insert_info = [[0 for _ in range(4)] for n in range(lut_max_V)]
                        mapping_info[current_level-1][int(num)-2][i] = val[0]
                        mapping_info.insert(current_level,insert_info)
                        mapping_info[current_level][int(num)-2][i] = val[1]
                        return mapping_info
            

            if lut_level_up == 1: mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
            mapping_info.insert(current_level,insert_info)
        else:
            insert_info[int(num)-2] = [_a + _b for _a, _b in zip(insert_info[int(num)-2], mapping_info[current_level-1][int(num)-2])]  # accumulate
            mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
            if lut_level_up == 1: mapping_info.insert(current_level,insert_info)

    if area_VH[1][1] == "V":
        insert_info[int(num)-2] = [_a + _b for _a, _b in zip(insert_info[int(num)-2], mapping_info[current_level-1][int(num)-2])]  # accumulate
        mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
        if lut_level_up == 1: mapping_info.insert(current_level,insert_info)
    else: #H
        for i in range(lut_max_V):
            insert_info[i][int(num)-2] += mapping_info[current_level-1][i][int(num)-2]  # accumulate
            mapping_info[current_level-1][i][int(num)-2] = 0
        if lut_level_up == 1: mapping_info.insert(current_level,insert_info)



    return mapping_info


########################
_MAP_FAIL_COUNT = [0]  # guard against repeated level placement failures (livelock)

@timer
def map_to_crossbar(luts, crossbar, mapping_info):
        global Copy_IR_Cycle
        global Reset_Cycle
        global VNOR_Cycle
        global HNOR_Cycle
        global A_star_Cycle
        global Place_Cycle
        global place_info_dict


        ordered_luts = sorted(luts.values(), key=lambda x: (x.level, x.size[0], x.size[1])) ########
        
        @timer
        def reset_crossbar_except_last_col(target_level):   #target_level : level of the LUT to map - 1
            reset_c=[]
            reset_IR_list = []
            global Attempt
            global Cycle
            global Path
            global Copy_IR_Cycle
            global Reset_Cycle

            for r in range(len(crossbar.grid)):
                for c in range(len(crossbar.grid[0])-1, -1, -1):
                    if not crossbar.grid[r][c]: cell_content=None
                    elif crossbar.grid[r][c] and '_' in crossbar.grid[r][c]:      #mapped lut
                        cell_content = crossbar.grid[r][c].split('_')[0]
                        if cell_content is not None and cell_content[0] =='~': 
                            cell_content = cell_content[1:]
                    elif crossbar.grid[r][c] and '_' not in crossbar.grid[r][c]:  #reset the cells left by A* routes
                        crossbar.grid[r][c] = None
                        cell_content = None
                        if c not in reset_c:
                            reset_c.append(c)                                                                             ###cell_content definition. A* reset
                    
                    if cell_content and '_' in crossbar.grid[r][c] :
                        if luts[cell_content].fanlevel < target_level: #no need to keep
                            cell_content2 = crossbar.grid[r][c].split('_')[1] 
                            if cell_content2 != "0" and cell_content2[0] == "~": cell_content2 = cell_content2[1:]
                            if cell_content2 == cell_content:
                                if c > int(len(crossbar.grid[0])*0.75):
                                    if (r,c) not in reset_IR_list:
                                        reset_IR_list.append((r,c)) # stacked IRs are handled as exceptions
                                elif c <= luts[cell_content].col[-1]: 
                                    crossbar.grid[r][c] = None
                                    if c not in reset_c:
                                        reset_c.append(c)  
                
                            else:
                                crossbar.grid[r][c] = None #reset the mapped lut
                                if c not in reset_c:
                                    reset_c.append(c)  
                        

                        else: # move IR
                            if c < int(len(crossbar.grid[0])*0.75):
                                if c == luts[cell_content].col[-1]: ###########last_c
                                    if crossbar.grid[r][c].split('_')[1]!='0' :  
                                        for _c in range(len(crossbar.grid[0])-1, -1, -1):   #search from the last col downward for a place to move the IR
                                                if crossbar.grid[r][_c] is None:
                                                    temp = crossbar.grid[r][c] #store the value (IR) in a temporary variable
                                                    if c<_c:
                                                        if _c > int(len(crossbar.grid[0])*0.75):##  restrict placement to beyond the 75% column
                                                            crossbar.grid[r][_c] = temp #push the temporary value to the end column
                                                            if crossbar.grid[r][c].split('_')[1][0]=='~':
                                                                crossbar.grid[r][_c] = crossbar.grid[r][_c].replace("_~","_")

                                                            else:
                                                                crossbar.grid[r][_c] = crossbar.grid[r][_c].replace("_","_~")

                                                            luts[cell_content].col.append(_c) #added so that this column is not reset
                                                            temp = temp.split('_')[0]
                                                            file_path = os.path.join(Path, "instructions.txt")

                                                            # open the file and write the data
                                                            with open(file_path, 'a') as f:
                                                                # format the string with an f-string
                                                                data = f"C{Cycle} Copy IR:{str(temp)} ({r}, {c}) to ({r}, {_c}) \n"
                                                                f.write(data)
                                                                Cycle += 1  # increment Cycle
                                                                Copy_IR_Cycle += 1

                                                            crossbar.grid[r][c] = None
                                                            if c not in reset_c:
                                                                reset_c.append(c)    
                                                            break

                                                        else:
                                                            done = False
                                                            for c_des in range(len(crossbar.grid[0])-1, -1, -1):
                                                                if done==True: break
                                                                for r_des in range(len(crossbar.grid)):
                                                                    if crossbar.grid[r_des][c_des] == None:
                                                                        temp2 = crossbar.grid[r][c_des]
                                                                        if temp2 == None:
                                                                            if temp.split("_")[1][0] == "~":
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_~","_")
                                                                            else: 
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_","_~")
                                                                            #crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_~","_")
                                                                            done=True
                                                                            break
                                                                        else: #IR already
                                                                            if temp2.split("_")[1][0] == "~":
                                                                                crossbar.grid[r_des][c_des] = crossbar.grid[r][c_des].replace("_~","_")
                                                                            else: 
                                                                                crossbar.grid[r_des][c_des] = crossbar.grid[r][c_des].replace("_","_~")

                                                                            if (r, c_des) in reset_IR_list:
                                                                                reset_IR_list.remove((r, c_des))
                                                                                reset_IR_list.append((r_des, c_des))

                                                                            crossbar.grid[r][c_des] = None

                                                                            if crossbar.grid[r][c].split('_')[1][0]=='~':
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_~","_")
                                                                                done=True
                                                                                break
                                                                            else:
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_","_~")
                                                                                done=True
                                                                                break

                                                            luts[cell_content].col.append(c_des+1) #added so that this column is not reset
                                                            file_path = os.path.join(Path, "instructions.txt")

                                                            if temp2 == None:
                                                                with open(file_path, 'a') as f:
                                                                # format the string with an f-string
                                                                    data = f"C{Cycle} Copy IR:{temp} ({r}, {c}) to ({r}, {c_des+1}) \n"
                                                                    f.write(data)
                                                                    Cycle += 1  # increment Cycle
                                                                    Copy_IR_Cycle += 1
                                                            else:
                                                                temp2 = temp2.split("_")[0]
                                                                temp = temp.split("_")[0]
                                                                # open the file and write the data
                                                                with open(file_path, 'a') as f:
                                                                    # format the string with an f-string
                                                                    data = f"C{Cycle} Copy IR:{temp2} ({r}, {c_des+1}) to ({r_des}, {c_des+1}) \n"
                                                                    f.write(data)
                                                                    Cycle += 1  # increment Cycle
                                                                    Copy_IR_Cycle += 1

                                                                    data = f"C{Cycle} Reset IR: ({r}, {c_des+1})\n"
                                                                    f.write(data)
                                                                    Cycle += 1
                                                                    Reset_Cycle += 1

                                                                    data = f"C{Cycle} Copy IR:{temp} ({r}, {c}) to ({r}, {c_des+1}) \n"
                                                                    f.write(data)
                                                                    Cycle += 1 
                                                                    Copy_IR_Cycle += 1

                                                            crossbar.grid[r][c] = None
                                                            if c not in reset_c:
                                                                reset_c.append(c)    
                                                            break

                                                        
                                                            
                                    else:
                                        crossbar.grid[r][c] = None
                                        if c not in reset_c:
                                            reset_c.append(c)    


                                elif c != luts[cell_content].col[-1]: 
                                    crossbar.grid[r][c] = None
                                    if c not in reset_c:
                                       reset_c.append(c)

            
            #for (r,c) in reset_IR_list:
            #filtered_list = [(fr, fc) for (r, c) in reset_IR_list if c <= int(len(crossbar.grid[0]) * 0.75)]
            from collections import defaultdict
            # group by c
            group_by_c = {}
            group_by_c = defaultdict(list)
            for r, c in reset_IR_list:
                group_by_c[c].append((r, c))

            reset_list = []
            reset_group_c_str = ""
            for key, source in group_by_c.items():
                if len(source) > 3:
                    for (gcr,gcc) in source: 
                        crossbar.grid[gcr][gcc] = None
                        reset_list.append((gcr,gcc))
                    reset_group_c_str = ', '.join(map(str, reset_list))
                    file_path = os.path.join(Path, "instructions.txt")
                    with open(file_path, 'a') as f:
                        data = f"C{Cycle} Reset IR (c={key}): {reset_group_c_str}\n"
                        f.write(data)
                        Cycle += 1
                        Reset_Cycle += 1
                        reset_group_c_str = ""
                        reset_list = []

            # group by r
            #group_by_r = defaultdict(list)
            #for r, c in reset_IR_list:
                #group_by_r[r].append((r, c))        


            reset_c = sorted(reset_c)
            reset_c_str = ', '.join(map(str, reset_c))
            
            file_path = os.path.join(Path, "instructions.txt")

            with open(file_path, 'a') as f:
                data = f"C{Cycle} Reset Column: {reset_c_str}\n"
                f.write(data)
                Cycle += 1
                Reset_Cycle += 1
                        

        global current_level_luts2
        i = 0
        modified = False
        done = False
        current_level = None
        level_up_list = []
        lut_level_up = 0
        level_up = False
        used_luts = []
        current_level_luts = []
        current_level_luts2 = []
        while done == False:
            if done == True: break
            else:
                if modified == True: 
                    modified = False
                else:
                    for lut in ordered_luts:
                        edit =0
                        if current_level is None:
                            current_level = lut.level
                            this_mapping_info = mapping_info[0]
                            area_VH = Blocking(this_mapping_info, crossbar)

                        while True:
                            if (None in area_VH and current_level ==1):
                                if None not in area_VH:
                                    past_area_VH = area_VH
                                area_VH = Blocking(this_mapping_info, crossbar)
                                print(area_VH)
                                if None in area_VH:
                                    ##if lut_level_up == 0: original_luts(luts, current_level,lut_level_up)
                                    ordered_luts = levelup_luts(luts, current_level,area_VH,lut_level_up,crossbar,ordered_luts) 
                                    lut_level_up += 1
                                    edit = 0
                                    if isinstance(area_VH[1], dict):
                                        for k, v in area_VH[1].items():
                                            if v != 0:
                                                if k.startswith('V'):
                                                    num = int(k[1:])  # 'V3' → 3
                                                    area_VH = (area_VH[0], [num, "V"])
                                                    edit = 1
                                                    break
                                    mapping_info = update_mapping_info(mapping_info, current_level, area_VH, edit, lut_level_up)
                                    modified = True
                                else: break
                            else: 
                                lut_level_up = 0
                                if lut.level == 1:
                                    if lut not in current_level_luts:
                                        current_level_luts.append(lut)
                                break
                        

                        if modified == True: break

                        if lut.level != current_level:
                        #or (lut.level == current_level and level_up == True):
                            if modified == True: break
                            #if level_up == False: 
                            current_level = lut.level
                            if None not in area_VH:
                                past_area_VH = area_VH

                            #current_level_luts3 = []
                            #for clut in current_level_luts:
                             #   if clut.level == current_level -1:
                              #      current_level_luts3.append(clut)
                                

                            print(f"Trying to use area: {area_VH}")
                            print(f"Current level luts: { [lut.name for lut in current_level_luts] }")
                            for current_level_lut in current_level_luts:
                                print(f"Trying to map LUT at level {current_level_lut.level} with fanin {current_level_lut.fanin} and expression {current_level_lut.expr}")
                                print(f"Number of terms: {current_level_lut.num_product_terms}")  # in case 'number of terms' is read as the list length
                                print(f"Terms: {current_level_lut.term_products}")
                            if not crossbar.map_lut_batched(current_level_luts, area_VH): ##placement happens here
                                print("Mapping failed even after resetting!")
                                _MAP_FAIL_COUNT[0] += 1  # stop instead of retrying the same level forever
                                if _MAP_FAIL_COUNT[0] > 20:
                                    print("[FATAL] Mapping failed: level placement failed more than 20 times")
                                    sys.exit(1)
                                break

                            current_level_luts = []
                            pass  # no crossbar print (a 256x256 print per level makes the log very large)
                            print('\n'+'\n')
                            crossbar = execute_Copy(luts, crossbar) #A*
                            place_info_dict = {}
                            pass  # no crossbar print (a 256x256 print per level makes the log very large)
                            print('\n'+'\n')
                            execute_NOR(current_level -1, luts, crossbar) #NOR
                            current_level_luts2 = []
                            reset_crossbar_except_last_col(lut.level-1) #Reset
                            pass  # no crossbar print (a 256x256 print per level makes the log very large)
                            print('\n'+'\n')      
                            print(f"\nFinished mapping level: {current_level-1}\n")
                            #  input("Press Enter to continue...")
                            print_runtime_summary()
                            print_section_summary()
                            print_ilp_summary()
                            
                            while True: #next_level_area 
                                this_mapping_info = mapping_info[current_level-1]
                                area_VH = Blocking(this_mapping_info, crossbar)
                                print(area_VH)
                                if None in area_VH:
                                    ordered_luts = levelup_luts(luts,current_level,area_VH,lut_level_up,crossbar,ordered_luts)
                                    lut_level_up += 1
                                    level_up_list.append(current_level-1)
                                    edit = 0
                                    if isinstance(area_VH[1], dict):
                                        for k, v in area_VH[1].items():
                                            if v != 0:
                                                if k.startswith('V'):
                                                    num = int(k[1:])  # 'V3' → 3
                                                    area_VH = (area_VH[0], [num, "V"])
                                                    edit = 1
                                                    break
                    
                                    mapping_info = update_mapping_info(mapping_info, current_level, area_VH, edit, lut_level_up)
                                    modified = True
                                    level_up = True
                                else:
                                    break
                        ##if modified == True: break

                ## handling for a single count
                
                            if modified == True: break  
                            level_up = False
                        
                        used_luts.append(lut)
                        if lut.level == current_level: 
                            if lut.level > 1:
                                if lut not in current_level_luts:
                                    current_level_luts.append(lut)
                            if lut not in current_level_luts2:
                                current_level_luts2.append(lut)

                     
                        if lut == ordered_luts[-1]:
                            if not crossbar.map_lut_batched(current_level_luts, area_VH): ##placement happens here
                                print("Mapping failed even after resetting!")
                                _MAP_FAIL_COUNT[0] += 1  # stop instead of retrying the same level forever
                                if _MAP_FAIL_COUNT[0] > 20:
                                    print("[FATAL] Mapping failed: level placement failed more than 20 times")
                                    sys.exit(1)
                                break
                            current_level=lut.level
                            print("\nFinished mapping the last level\n")
                            crossbar = execute_Copy(luts, crossbar)
                            pass  # no crossbar print (a 256x256 print per level makes the log very large)
                            execute_NOR(current_level - 1, luts, crossbar)
                            print(level_up_list)  

                            
                            file_path = os.path.join(Path, "instructions.txt")
                            with open(file_path, 'a') as f:
                                data = f"Place Cycle {Place_Cycle} / A_star_Cycle {A_star_Cycle} / Copy_IR_Cycle {Copy_IR_Cycle} / HNOR_Cycle {HNOR_Cycle} / VNOR_Cycle {VNOR_Cycle} / Reset_Cycle {Reset_Cycle} \n"
                                f.write(data)

                            if len(used_luts) == len(luts):
                                print ('all luts mapping successfully')
                            else:
                                print ("mapping fail")


                            done = True
                            #reverse the final outputs

                            print_runtime_summary()
                            print_section_summary()
                            

                        if current_level == ordered_luts[0].level and ordered_luts[1].level == ordered_luts[0].level+1: 
                            del ordered_luts[0]
                            break   
                        


        temp_level_luts = ''.join(str(ordered_luts)+'\n')
        print('\n' + '\n' + '\n')
        print('--'*32)
        print("@@@@@@@@LUTS sorted by level")
        
        print(temp_level_luts)
    
        return crossbar


def print_runtime_summary():
    print("\n[ Function runtime summary ]")
    total_all = sum(data["total_time"] for data in runtimes.values())
    print(f"Total accumulated runtime: {total_all:.6f} s\n")
    for name, data in runtimes.items():
        count = data["count"]
        total = data["total_time"]
        avg = total / count if count else 0.0
        print(f"{name} {count} calls | total {total:.6f} s | avg {avg:.9f} s")

def print_section_summary():
    print("\n[ Section runtime summary ]")
    for name, times in section_times.items():
        count = len(times)
        total = sum(times)
        avg = total / count if count else 0.0
        print(f"{name} | {count} calls | total {total:.6f} s | avg {avg:.9f} s")




def plot_crossbar(crossbar):
    from igraph import Graph, plot

    rows = len(crossbar.grid)
    cols = len(crossbar.grid[0])

    g = Graph()

    # Add vertices
    for r in range(rows):
        for c in range(cols):
            g.add_vertex(name=str(crossbar.grid[r][c]) if crossbar.grid[r][c] is not None else f"{r},{c}")

    # Add edges
    for r in range(rows):
        for c in range(cols): 
            if c < cols - 1:
                g.add_edge(r*cols + c, r*cols + c + 1)  # horizontal
            if r < rows - 1:
                g.add_edge(r*cols + c, (r+1)*cols + c)  # vertical

    layout = g.layout_grid()
    visual_style = {}
    visual_style["bbox"] = (300, 300)
    visual_style["margin"] = 20
    visual_style["vertex_size"] = 20
    visual_style["vertex_label"] = [v["name"] for v in g.vs]
    

    plot(g, layout=layout, target="my_output.png", **visual_style)


verilog_input = open(_args.input, encoding="utf-8").read()
 
verilog_text1 = parse_verilog(verilog_input)
print(f"**************************\n")
print(f"Primary inputs = {primary_inputs}")
print(f"**************************\n")
verilog_text2 = update_lut_info(verilog_text1)
print(verilog_text2)  
mapping_info = Optimizing_luts(verilog_text2)
verilog_text3 = annotate_lut_terms(verilog_text2)
print("-"*444)
#print(mapping_info)
crossbar_instance = initialize_crossbar(_cb_rows, _cb_cols)
verilog_crossbar3 = map_to_crossbar(verilog_text3, crossbar_instance, mapping_info)