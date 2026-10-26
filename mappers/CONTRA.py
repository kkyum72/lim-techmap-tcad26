"""CONTRA - our re-implementation of the CONTRA mapper (D. Bhattacharjee et al., ICCAD 2020), written for the
comparison in the TCAD 2026 paper. This is not the original authors' code (https://github.com/debjyoti0891/arche).

  python3 mappers/CONTRA.py -i benchmarks/netlists/ISCAS85/c432.v -c 64x64
"""
import re
import sys
#from collections import OrdereDict
import os
import re
import time

global Attempt
global Cycle
global lut_max_V
global lut_last_col
global current_level_luts

# Current user's home directory
home_dir = os.path.expanduser("~")

# Paths
# ---- command line ----
import argparse as _argparse
import os as _os
_METHOD = "CONTRA"
_parser = _argparse.ArgumentParser(description="CONTRA: technology mapping of a NOR-of-NOR LUT netlist onto a memristor crossbar")
_parser.add_argument("-i", "--input", required=True, help="NoN LUT netlist (.v), e.g. benchmarks/netlists/ISCAS85/c432.v")
_parser.add_argument("-c", "--crossbar", required=True, help="crossbar size RxC, e.g. 64x64")
_parser.add_argument("-o", "--out", default=None, help="output directory (default: results/<bench>_<RxC>_<method>)")
_parser.add_argument("--threads", type=int, default=4, help="Gurobi threads (default 4); not used by this mapper")
_args = _parser.parse_args()
_cb_rows, _cb_cols = (int(x) for x in _args.crossbar.lower().split("x"))
_bench_name = _os.path.splitext(_os.path.basename(_args.input))[0].replace("_mapped_named", "")
_suffix = ""
_RESULT_DIR = _os.path.abspath(_args.out or _os.path.join("results", f"{_bench_name}_{_cb_rows}x{_cb_cols}_{_METHOD}{_suffix}"))
_os.makedirs(_RESULT_DIR, exist_ok=True)


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
def _outer_negated(expr):
    """True if expr is ~( ... ) with the parenthesis spanning the whole expression."""
    e = expr.strip()
    if not e.startswith('~('):
        return False
    depth = 0
    for i, ch in enumerate(e[1:], start=1):
        depth += ch == '('
        depth -= ch == ')'
        if depth == 0:
            return i == len(e) - 1
    return False
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
lut_max_V = 0
lut_last_col = []
current_level_luts = []

import time

# Per-function runtime records
runtimes = {}
section_times = {}


import time
from functools import wraps

import sys
# Open the log file
# Open the log file
log_file_path = _os.path.join(Path, "log.txt")
log = open(log_file_path, 'w', encoding='utf-8', buffering=1)  # line-buffered

# Redirect both stdout and stderr to the log file
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




####### Initial parsing ######
####### Initial parsing ######
####### Initial parsing ######
def parse_verilog(verilog_str):         
    global Path
    global lut_max_V
    assigns = re.findall(r'assign\s+~(.*?)\s+=\s+(.*?);', verilog_str)
    luts = {}
    print("Parsing result:", assigns)

    # Build the file path
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
            product_terms = 1
        else: product_terms = len(expr.split('|'))   
        
        faninlen = len(fanin)
        if output in luts:
            print(f"Error: Multiple assignments for {output}")
            return 
        luts[output] = LUT(name=output, fanin=fanin, product_terms=product_terms, size=[faninlen + 1, product_terms + 1], level=555, expr=expr, row=[99], col=[99], fanlevel = 0)
        if lut_max_V < product_terms + 1: 
            lut_max_V = product_terms + 1
    return luts


######level, fanlevel, size optimization#######
def update_lut_info(luts):              
    for lut_name, lut in luts.items():
        width = len(lut.fanin) + 1
        height = lut.product_terms + 1
        
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

######2x2   3x2   4x2   5x2
######2x3   3x3   4x3   5x3
######2x4   3x4   4x4   5x4
######2x5   3x5   4x5   5x5
######Information for block-style placement######


def Optimizing_luts(luts):   
    global lut_max_V       
    ordered_luts = sorted(luts.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
    max_level = ordered_luts[-1].level
    mapping_info = [[[0 for _ in range(4)]for _ in range(lut_max_V)]for _ in range(max_level)]  # 3D list: max_level lists of 4x4

    for lut in ordered_luts:
        level = lut.level - 1
        row = lut.size[1] -2
        col = lut.size[0] -2
        mapping_info[level][row][col] += 1   #index note: level+1 = level, row+2 = height, col+2 = width

    return mapping_info    

class LUT:
    def __init__(self, name, fanin, product_terms, size, level, expr, row, col, fanlevel):
        self.name = name
        self.fanin = fanin
        self.product_terms = product_terms
        self.size = size
        self.level = level
        self.expr = expr
        self.row = row
        self.col = col
        self.fanlevel = fanlevel
        
    def __repr__(self): 
        return f"LUT(name={self.name}, fanin={self.fanin}, product_terms={self.product_terms}, size={self.size}, level={self.level}, expr={self.expr}, row={self.row}, col={self.col}, fanlevel={self.fanlevel})"

def initialize_crossbar(rows, cols):

    return Crossbar(rows, cols)




#######Crossbar size allocation, LUT mapping#######
#######Crossbar size allocation, LUT mapping#######
class Crossbar:
    def __init__(self, rows, cols):
        self.grid = [[None for _ in range(cols)] for _ in range(rows)]

    def __str__(self):
        return '\n'.join([' '.join(["%-8s" % cell if cell else '    .   ' for cell in row]) for row in self.grid])

    def __repr__(self):
        return self.__str__() 
    
    #######Final LUT mapping happens here#######
    @timer
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
                    #this_r=can_r.copy()
                    #for r in can_r:
                     #   if r < j:
                      #      this_r.remove(r)

                    temp = self.place_lut(can_r, this_c, lut)
                    if temp == False: return False
                    return True 
                else: False
                if not can_c and j == len(self.grid)-1 and i == len(self.grid[0])-1:
                    return False
        return False


#######Whether the LUT can be placed#######
    def can_place(self, lut, area_VH):  
        global lut_last_col
        size = lut.size
        can_c = []
        can_r =[] 
        temp_r=0
        temp_c=0

        for row in range(lut_max_V-1):
            #if row+2 < int(size[1]): #count the V already occupied
             #       temp_r+=int(area_VH["V"+str(row+2)])
            if row+2 == int(size[1]):
                #if int(area_VH["V"+str(row+2)]) == 0: ###############ROW 1
                 #   for n in range(temp_r):
                  #      if temp_r+n <= len(self.grid):
                   #         can_r.append(temp_r+n)
                #else:
                    for n in range(int(area_VH["V"+str(size[0])])):
                        if temp_r+n <= len(self.grid):
                            can_r.append(temp_r+n)


        for col in range(4):
            t=0
            #if size[0] ==  4 : 
             #   t+=1 
            #if lut.name == "n296":
                #t += 1
                #for roww in self.grid:
                   #print(roww)
            if col+2 < int(size[0]):
                temp_c+=int(area_VH["H"+str(col+2)])
            elif col+2 == int(size[0]):
                if len(lut_last_col) != 0:
                    temp_c_copy = temp_c
                    lut_last_col.sort()  
                    for check_col in lut_last_col:
                        if check_col <= temp_c:
                            temp_c += 1


                if int(area_VH["H"+str(col+2)]) == 0:
                    for n in range(temp_c):
                        if temp_c+n <= len(self.grid[0]):
                            can_c.append(temp_c+n)
                else:
                    #for n in range(int(area_VH["H"+str(col+2)])):
                        n = 0
                        while (len(can_c) < int(area_VH["H"+str(col+2)])):

                            if temp_c+n < len(self.grid[0]):
                                if temp_c+n in lut_last_col:
                                    n+=1
                                
                                else:
                                        can_c.append(temp_c+n)
                                        n+=1
                            else: 
                                break

        return can_c, can_r
###############

    def optimize_can_c(self, can_r, this_c, lut):
        can_c=[]
        flag=0
        check = 0
        global lut_last_col
        for idx, r in enumerate(can_r):
            flag=0
            can_c=[]
            check = -1
            for idx2,c in enumerate (this_c):
                if r <= len(self.grid) - (lut.size[1]) -1:
                    if all(self.grid[r+i][c] is None for i in range(lut.size[1])):
                        #if idx == 0 or idx2 % (lut.size[0]) == 0:
                        flag+=1
                        check+=1
                        can_c.append(c)
                        if idx2 % (int(lut.size[0])) == 0: check = idx2
                        if check != idx2 : break
                        if flag == (int(lut.size[0])):
                            this_r = r
                            return can_c, this_r 
                    else: flag=0
        return [], None  # no fitting position found

#######Place LUT + fanins#######
    # ===== Helpers: put these ABOVE place_lut =====
    @staticmethod
    def _unwrap_parens(s: str) -> str:
        """Remove redundant outer parentheses around the whole string (nested ones too)."""
        s = s.strip()
        while s.startswith("(") and s.endswith(")"):
            depth = 0
            ok = True
            for i, ch in enumerate(s):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and i != len(s) - 1:
                        ok = False
                        break
            if ok:
                s = s[1:-1].strip()
            else:
                break
        return s

    @staticmethod
    def _fix_parens(s: str) -> str:
        """Fix unbalanced opening/closing parentheses."""
        depth = 0
        fixed = []
        for ch in s:
            if ch == "(":
                depth += 1
                fixed.append(ch)
            elif ch == ")":
                if depth > 0:
                    depth -= 1
                    fixed.append(ch)
                else:
                    # ignore an unmatched )
                    continue
            else:
                fixed.append(ch)
        # close the remaining open parentheses
        fixed.extend(")" * depth)
        return "".join(fixed)

    @staticmethod
    def split_top(expr: str, op: str):
        """Split on op ('&' or '|') only at the top level, respecting parenthesis depth."""
        res = []
        buf = ""
        depth = 0
        for ch in expr:
            if ch == "(":
                depth += 1
                buf += ch
            elif ch == ")":
                depth -= 1
                buf += ch
            elif ch == op and depth == 0:
                res.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf:
            res.append(buf.strip())
        return res

    @staticmethod
    def parse_expr(expr: str):
        """
        Parse a Verilog logic expression (AND/OR/NOT only) → {negated: bool, terms: list[list[str]]}
        - terms: list split on OR; each item is a list split on AND
        - each literal is returned without parentheses or spaces (e.g. 'IG77', '~IG77', 'n83')
        e.g. ~( (A & B) | (C & D) )
            -> {'negated': True, 'terms': [['A','B'], ['C','D']]}
        """
        expr = Crossbar._fix_parens(expr.strip())      # balance the parentheses first
        expr = Crossbar._unwrap_parens(expr)

        # detect negation of the whole expression ~(...)
        is_negated = False
        if expr.startswith("~(") and expr.endswith(")"):
            is_negated = True
            expr = Crossbar._fix_parens(expr[1:].strip())
            expr = Crossbar._unwrap_parens(expr)

        # split on top-level OR (a single term if there is no OR)
        or_terms = Crossbar.split_top(expr, '|')

        parsed_terms = []
        for t in or_terms:
            t = Crossbar._unwrap_parens(t)
            and_terms = Crossbar.split_top(t, '&')  # a single literal if there is no AND
            cleaned = [Crossbar._unwrap_parens(x).replace(" ", "") for x in and_terms if x.strip() != ""]
            parsed_terms.append(cleaned)

        return {"negated": is_negated, "terms": parsed_terms}


    def place_lut(self, can_r, this_c, lut):
        import os
        global Cycle, Attempt, Path, Place_Cycle, lut_last_col

        # --- helper: normalize a literal (reduce the leading ~ by parity) ---
        def normalize_lit(lit: str) -> str:
            s = lit.strip()
            # count leading tildes
            i = 0
            while i < len(s) and s[i] == "~":
                i += 1
            rest = s[i:].strip()
            if rest == "":
                return rest
            # even number of ~ -> no negation, odd -> a single ~
            return ("~" + rest) if (i % 2 == 1) else rest

        # candidate columns and row
        can_c, row = self.optimize_can_c(can_r, this_c, lut)
        if not can_c:  # no fitting position
            return False

        # split expr into product terms
        raw_expr = (lut.expr.strip()[1:] if _outer_negated(lut.expr) else lut.expr).replace('(', '').replace(')', '')
        and_present = ('&' in lut.expr)
        if and_present:
            or_terms = [t.strip() for t in raw_expr.split('|')]
            # sort by number of ANDs, descending
            or_terms.sort(key=lambda x: x.count('&'), reverse=True)
        else:
            # pure OR (or a single literal): keep the string (only the first row is used)
            or_terms = raw_expr  # string

        temp_row = []
        temp_col = []

        # placement log
        for rlog in range(lut.size[1]-1):
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a') as f:
                data = f"C{Cycle} Place LUT{lut.name} ({row + rlog}, {can_c[0]}), ({row + rlog}, {can_c[-1]}) \n"
                f.write(data)
                Cycle += 1
                Place_Cycle += 1

        # actual placement
        for idx, r_off in enumerate(range(lut.size[1])):
            # literals of this row
            if and_present:
                term_elements = or_terms[idx].split('&') if idx < len(or_terms) else []
            else:
                term_elements = or_terms.split('|') if idx < 1 else []

            # --- normalize: remove double negation such as ~~ from literals ---
            term_elements = [normalize_lit(x) for x in term_elements if x.strip() != ""]

            if row + r_off not in temp_row:
                temp_row.append(row + r_off)

            for idx2, c in enumerate(can_c):
                if row + r_off >= len(self.grid) or c >= len(self.grid[0]):
                    return False

                # ---- input placement: first column only ----
                if idx2 == 0 and term_elements:
                    # value: list of normalized literals
                    value = [v for v in term_elements if v != ""]
                    loop = 0
                    # safety limit against an infinite loop (with some margin)
                    safety_limit = max(1, len(value) * 4 + 4)

                    while len(value) != 0 and loop < safety_limit:
                        placed_this_round = False
                        r_correct = []
                        c_correct = []
                        loop += 1

                        # scan all of value for an item that can be placed
                        for v_i, value2 in enumerate(list(value)):  # iterate over a copy
                            place = False
                            # value2 is already normalized, so pos_value follows from its first character
                            pos_value = value2[1:] if value2.startswith("~") else value2

                            for cc in can_c[:-1]:  # skip the last column (output)
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
                                            # compare tok with pos_value (tok is "~X" or "X")
                                            if tok == pos_value or tok == "~" + pos_value:
                                                # same row to the left of cc, or same column
                                                if rr == row + r_off and i < cc:
                                                    r_correct.append((row + r_off, cc))
                                                elif i == cc:
                                                    c_correct.append((row + r_off, cc))

                            # placement priority
                            target = None
                            if len(r_correct) == 0 and len(c_correct) != 0:
                                target = c_correct[0]
                            elif len(r_correct) != 0 and len(c_correct) == 0:
                                target = r_correct[0]
                            elif len(r_correct) != 0 and len(c_correct) != 0:
                                # prefer the intersection (in effect the same as choosing c_correct[0])
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
                                # value2 is already normalized, so use it as is
                                self.grid[tr][tc] = lut.name + "_" + value2
                                del value[v_i]
                                place = True
                                placed_this_round = True
                                break  # value loop

                        # still not placed at the fallback point: force value[0] into the first empty cell
                        if not placed_this_round and loop > len(term_elements):
                            fallback_target = None
                            for ccc in can_c[:-1]:
                                if self.grid[row + r_off][ccc] is None:
                                    fallback_target = (row + r_off, ccc)
                                    break
                            if fallback_target:
                                tr, tc = fallback_target
                                self.grid[tr][tc] = lut.name + "_" + value.pop(0)

                # ---- fill the LUT cells (output/padding) ----
                if self.grid[row + r_off][c] is None:
                    if idx2 == lut.size[0] - 1:
                        if r_off == lut.size[1] - 1:
                            # output cell: "~name" unless the whole expression is negated
                            self.grid[row + r_off][c] = (lut.name + "_" + lut.name) if _outer_negated(lut.expr) else (lut.name + "_~" + lut.name)
                        else:
                            self.grid[row + r_off][c] = lut.name + "_0"
                    elif r_off == lut.size[1] - 1:
                        self.grid[row + r_off][c] = None
                    else:
                        self.grid[row + r_off][c] = lut.name + "_0"

        # cell labels of the placed rows as a comment line,
        # read by verify/replay.py; not an instruction, so cycle counts are unaffected
        with open(os.path.join(Path, "instructions.txt"), 'a') as _f:
            for _r in range(lut.size[1] - 1):
                _labs = []
                for _c in can_c:
                    _v = self.grid[row + _r][_c]
                    _labs.append(_v.split('_', 1)[1] if _v and '_' in _v else '0')
                _f.write(f"#LABELS LUT{lut.name} ({row + _r}, {can_c[0]}), ({row + _r}, {can_c[-1]}) cols {','.join(map(str, can_c))} | {' '.join(_labs)}\n")
        lut.row = temp_row
        lut.col = can_c

        return True



      

#######A* definition#######
#######A* definition#######
def g(r, c, Rd, Cd, start_polarity, destination_polarity, crossbar):
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
    
    #elif (r == Rd or c == Cd) and (start_polarity == destination_polarity):
     #   return 2, destination_polarity
    
    #elif (r != Rd and c != Cd) and (start_polarity == destination_polarity):
     #   if crossbar.grid[Rd][c] or crossbar.grid[r][Cd] == None:
      #      return 2, destination_polarity
       # else: return 4, destination_polarity
    

    #elif (r != Rd and c != Cd) and (start_polarity != destination_polarity):
        #if crossbar.grid[Rd][c] or crossbar.grid[r][Cd] == None:
     #       return 3, destination_polarity
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
        if A_star_idx == 1 and (r,c) == source and "_" not in crossbar.grid[r][c] or crossbar.grid[r][c] is None :
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
        if A_star_idx == 1 and (r,c) == source and "_" not in crossbar.grid[r][c] or crossbar.grid[r][c] is None:
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
                    
    if crossbar.grid[r][c] is None:           
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
import time
from functools import lru_cache
import time, heapq
from functools import lru_cache
def A_star_search(source, destination, source_polarity, destination_polarity, crossbar):
    """
    Optimized version:
      - state (r,c,pol) encoded as an int
      - bulk neighbor push + heapify to minimize heapq operations
      - heuristic fallback: inline Manhattan distance
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

        # goal check
        if (r, c) == (Rd, Cd):
            same_pol = (source_polarity == destination_polarity)
            if (same_pol and (gcur % 2 == 0)) or ((not same_pol) and (gcur % 2 == 1)):
                path_rev = reconstruct_path(came_from, state_id)
                return path_rev
        #else: path_rev = []
        # expand neighbors (optimization: heappush only)
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





#######find a fanin and its sources, route it with A*, and map the copies#######
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
            # append the copy instruction to the file
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                data = "C%d Copy LUT%s (%d, %d) to (%d, %d)\n" % (Cycle, lut, check1, check2, r, c)
                f.write(data)
                Cycle += 1  # increment Cycle after writing
                A_star_Cycle += 1

        

        if idx >= len(path)-1: return crossbar
        #edit ~=source polarity=0

        elif idx > 0 and  idx < len(path) - 1 and idx % 2 == 0:  #even idx
                crossbar.grid[r][c] = even_source                

        elif idx > 0 and  idx < len(path) - 1 and idx % 2 == 1: #odd idx
            #if source_polarity == 0: ##~n
             #   lut1=source[1:]
                crossbar.grid[r][c] = odd_source
            #else:
             #   crossbar.grid[r][c] = "~"+source
        
    return crossbar
    
def find_fanin(row, col, luts, crossbar, cell_content):
    for r in range(len(crossbar.grid)):
        for c in range(len(crossbar.grid[0])):
            if row+r > len(crossbar.grid) - 1 or col+c > len(crossbar.grid[0]) - 1 : break
            elif crossbar.grid[row+r][col+c] is not None and '_' in crossbar.grid[row+r][col+c]:

                test_temp = crossbar.grid[row+r][col+c].split('_')
                if len(test_temp) < 2: continue

                cell_content = test_temp[0]
                cell_content2 = test_temp[1]

                if col+c != luts[cell_content].col[-1]:

                    if crossbar.grid[row+r][col+c].split('_')[0] != crossbar.grid[row+r][col+c].split('_')[1] or cell_content != cell_content2[1:]:
                    
                        #if '_' not in crossbar.grid[row+r][col+c] : continue
                        
                        if len(cell_content) > 0 and len(cell_content2) > 1:
                            if cell_content2[0] == "n" or cell_content2[0] == "O" or cell_content2[0:2] == "~n" or cell_content2[0:2] == "~O" : #c != luts[cell_content].col[-1] and 
                                current_level = luts[cell_content].level
                                fanin = crossbar.grid[row+r][col+c].split('_')[1] #fanin: fanin name, i.e. the name at the destination
                                if crossbar.grid[row+r][col+c].split('_')[1][0] == "~": 
                                    destination_polarity = 1  # a '~x' cell must hold x
                                    if cell_content2[0] == "~":
                                        print(cell_content2)
                                else: 
                                    destination_polarity = 0  # an 'x' cell must hold NOT x
                                #print(target) 
                                sources=find_sources((row+r,col+c), fanin, current_level, luts, crossbar, row, col)
                                return (row+r,col+c),fanin, destination_polarity, sources, cell_content #target: LUT that appears as a fanin, excluding intermediate results
                                                                    #row+r, col+c  == destination
        col=0    

def find_sources(location, fanin, current_level, luts, crossbar, row, col): 
    sources=[]  
    for r in range(len(crossbar.grid)):
        for c in range(len(crossbar.grid[0])):
            if (r,c) == location: continue # r,c = destination
            elif crossbar.grid[r][c] is not None:  
                #destination = crossbar.grid[row][col].split
                #if destination 

                if '_' in crossbar.grid[r][c]:
                #if fanin[0] == "~" and (crossbar.grid[r][c][-4:] == (fanin or fanin[1:]) or crossbar.grid[r][c][-3:] == fanin[1:]) or crossbar.grid[r][c][-3:] == fanin or crossbar.grid[r][c][-4:] == fanin or crossbar.grid[r][c][-5:] == fanin: 
                    #if fanin[0] == "~" and (crossbar.grid[r][c].split("_")[1] == fanin[1:]) or fanin[0] != "~" and (crossbar.grid[r][c].split("_")[1] == fanin):
                            if fanin[0] != "~" and (crossbar.grid[r][c].split("_")[1]== "~" + fanin or crossbar.grid[r][c].split("_")[1]== fanin) or fanin[0] == "~" and (crossbar.grid[r][c].split("_")[1] == fanin or crossbar.grid[r][c].split("_")[1] == fanin[1:]):
                                cell_content = crossbar.grid[r][c].split('_')[0]
                                if cell_content[0] == "~":
                                    cell_content = cell_content[1:]
                            
                                cell_content2 = crossbar.grid[r][c].split("_")[1]
                                if cell_content2[0] == "~":
                                    cell_content2 = cell_content2[1:]
                                
                                if cell_content2 == "n86":
                                    t=0


                                if cell_content == cell_content2: #saved IR    
                                    if crossbar.grid[r][c].split('_')[1][0] == "~": 
                                        source_polarity = 0   #0:~ 1:
                                        sources.append(((r,c),fanin, source_polarity))

                                    else: 
                                        source_polarity = 1
                                        sources.append(((r,c),fanin, source_polarity))
                                #else: #placed
                                 #   if luts[cell_content].level == current_level and (location[0] > r and location[1] == c) :
                                  #      if crossbar.grid[r][c].split('_')[1][0] == "~": 
                                   #         source_polarity = 0   #0:~ 1:
                                    #        sources.append(((r,c),fanin, source_polarity))

                                     #   else: 
                                      #      source_polarity = 1
                                       #     sources.append(((r,c),fanin, source_polarity))           


                else: # '_' not in crossbar.grid[r][c] = A*
                    if fanin[0] != "~" and (crossbar.grid[r][c]== "~" + fanin or crossbar.grid[r][c]== fanin) or fanin[0] == "~" and (crossbar.grid[r][c] == fanin or crossbar.grid[r][c] == fanin[1:]):
                        if c == location[1]:
                            if crossbar.grid[r][c][0] == "~": 
                                source_polarity = 0   #0:~ 1:as is
                                sources.append(((r,c),fanin, source_polarity))

                            else: 
                                source_polarity = 1
                                sources.append(((r,c),fanin, source_polarity))  #((row,col), lut name, source_polarity)
    return sources
 


def _exact_copy_route(source, destination, source_polarity, destination_polarity, crossbar):
    """Complete search used only when A* finds no path from
    any source. Same move model as get_neighbors: one copy (MAGIC NOT) moves a value to any empty cell
    in the same row or column and flips its polarity; the destination is the fan-in cell. Breadth-first
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


def _rescue_route(fanin, destination, destination_polarity, crossbar, sources, routed):
    """Called only when A* finds no path from any source (the run
    would stop with A*_search_Fail). First a complete search, then reset the dead copies
    left on the route cells of fan-ins routed earlier in this level (their fan-in cells already hold the value)
    one group at a time (one cycle each: the row, then the column of each source, then of the fan-in cell, then
    every column), searching again after each reset. LUT cells, stored IRs ('_' cells), routed fan-in cells and
    copies of this net are kept. Returns the path, or None if no route exists even after all resets."""
    global Cycle, Reset_Cycle
    grid = crossbar.grid
    R, C = len(grid), len(grid[0])
    net = fanin.lstrip("~")
    holders = [(s[0], s[2]) for s in sources]

    def route():
        for (r, c), pol in holders:
            p = _exact_copy_route((r, c), destination, pol, destination_polarity, crossbar)
            if p:
                return p
        return None

    path = route()
    if path:
        print(f"[route rescue] complete search: route for {fanin} -> {destination} without reset")
        return path
    groups = []
    for (r, c), _ in holders + [(destination, destination_polarity)]:
        groups += [("r=%d" % r, [(r, j) for j in range(C)]), ("c=%d" % c, [(i, c) for i in range(R)])]
    groups += [("c=%d" % c, [(i, c) for i in range(R)]) for c in range(C)]
    n_resets = 0
    for label, cells in groups:
        cells = [(r, c) for r, c in cells if grid[r][c] is not None and "_" not in grid[r][c]
                 and (r, c) not in routed and grid[r][c].lstrip("~") != net]
        if not cells:
            continue
        for r, c in cells:
            grid[r][c] = None
        with open(_os.path.join(Path, "instructions.txt"), "a") as f:
            f.write(f"C{Cycle} Reset Route ({label}): {', '.join(f'({r}, {c})' for r, c in cells)}\n")
        Cycle += 1
        Reset_Cycle += 1
        n_resets += 1
        path = route()
        if path:
            print(f"[route rescue] {fanin} -> {destination} after {n_resets} reset cycle(s)")
            return path
    print(f"[route rescue] failed: {fanin} -> {destination} ({n_resets} reset cycle(s))")
    return None


@timer
def execute_Copy(luts, crossbar): 
    _routed = set()  # fan-in cells already routed in this level
    row=0 
    col=0
    cell_content =""
    while True:
        result = []
        result = find_fanin(row, col, luts, crossbar,cell_content)
        

        if result is None:
            break 

        (row, col), fanin, destination_polarity, sources, cell_content = result  
        start_polarity = 1

        destination = row, col

        path=(0,0)

        t=0
        if len(sources)>=2:
                        t+=1
                        #print(crossbar)
                        #print('\n'+'\n')
        

        shortest_path =None
        shortest_path_len = float('inf')
        
        for idx,source in enumerate(sources):  #sources : ((row,col), lut name, source_polarity)
            start = source[0] #source position
            start_polarity = source[2]
            path = A_star_search(start, destination, start_polarity, destination_polarity, crossbar) #source[1])

            if (path is None or path == []) and False:  # disabled: a failed search is handled after the loop
                if len(sources) == 1 or (len(sources) > 1 and idx == len(sources) - 1  and shortest_path is None): 
                    pass  # crossbar dump omitted (log size)
                    print('\n'+'\n')    
                    shortest_path = (0,0)
                    with open(_os.path.join(Path, "failure.txt"), "a") as f:
                        pass  # crossbar dump omitted (log size)
                        print('\n'+'\n')
                        data ="A*_search_Fail: fanin = %s and lut = %s in level %d \n" % (fanin, cell_content, luts[cell_content].level)
                        f.write(data)
                        sys.exit()
                    
            else:      
                if path and len(path) < shortest_path_len:
                    shortest_path = path
                    shortest_path_len = len(path)
        
        if shortest_path is None and sources:  # no A* path from any source: rescue search
            shortest_path = _rescue_route(fanin, destination, destination_polarity, crossbar, sources, _routed)
        if shortest_path is None:  # no source found or no route found
            with open(_os.path.join(Path, "failure.txt"), "a") as f:
                f.write("A*_search_Fail: fanin = %s and lut = %s in level %d (no path)\n" % (fanin, cell_content, luts[cell_content].level))
            sys.exit()
        crossbar = mapping_A_star(shortest_path, fanin, crossbar, destination_polarity, start_polarity)
        _routed.add(destination)  # mark the fan-in cell as routed
        #if sources and sources[0][1] == "n100" or sources[0][1] == "~n100": 
            #print(crossbar)      
            #print('\n'+'\n') 
        col += 1
    
    return crossbar
    #######where all operations take place#######
#######reset, 
    

        


@timer
def init_SET(mapping_info):     #builds the SET from the initial LUTs
        global lut_max_V
        H = [2, 3, 4, 5]
        sizes = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]
        SET_Sequence = []   #sizes (out of all 16) that must be mapped now
        
        for i in range(len(mapping_info)):
            for j in range(4):
                if mapping_info[i][j] != 0:
                    size = sizes[i][j]
                    if size not in SET_Sequence:
                        SET_Sequence.append(size)
        return SET_Sequence

@timer
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
    
    print(count_size)
    return count_size




@timer
def define_dict(count_size, crossbar):    #builds the area_VH dictionary: area occupied per width and height; fold=0 not folded, fold==2 folded horizontally, fold==3 folded vertically
    global lut_max_V
    #count_size={}       #number of LUTs per size (out of all 16) to be mapped now   
    #count_VH={}         #how many of widths 2,3,4,5 and heights 2,3,4,5 are present, initial value = 0
    area_VH={}          #rows/cols occupied by each of the 8 sizes (widths 2,3,4,5 and heights 2,3,4,5) (not index)
    V_difference = []
    temp_global = 0      #area of the current set compared with the whole crossbar [width, height]
    sorted_temp_V = []
    sorted_V_difference = []
    area_H = {}

    H = [2, 3, 4, 5]
    keys_size = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]

    keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # heights
    keys2 = [f"H{h}" for h in H]                    # widths
    #count_VH = {key: 0 for key in keys}
    area_VH = {key: 0 for key in keys2}
    area_VH.update({key: 0 for key in keys1})

    for (h, v), cnt in count_size.items():
        key = f"H{h}"
        if key not in area_H:
            area_H[key] = 0
        area_H[key] += cnt * v
    
    for key, nums in area_H.items():
        if nums >= len(crossbar.grid):
            area_VH[key] = int(key[1]) * ((nums // len(crossbar.grid)) + 2)
            area_VH["V" + key[1]] = len(crossbar.grid)
        else: 
            area_VH[key] = int(key[1])
            area_VH["V" + key[1]] = area_H[key]
    

    #for size in count_size.keys():
     #   temp_global += int(size[1]) * int(size[0])
      #  area_VH["H" + str(size[0])] = int(size[0])


    """
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
            area_VH[area_H] = int(max(temp_V)) #actual occupied area, considering the V size
    """

            

    
    #if temp_global >= len(crossbar.grid[0])*len(crossbar.grid[1]) / 2 :  #run if the current LUTs occupy more than half of the crossbar
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

    keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # heights
    keys2 = [f"H{h}" for h in H]                    # widths

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
    for i in range(2,lut_max_V + 1):
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

    for label, values,max_H in new_temp_V:
        sorted_temp_V = sorted(values, reverse=True)

        if (len(sorted_temp_V) == 1): return [[label, sorted_temp_V[0]]]

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


@timer
def case_operate(SET_Sequence, crossbar, mapping_info, area_VH):
    global lut_max_V
    
    result_Sequence = [SET_Sequence]   # final Sequence (2D list)
    case = 0
    extend_cnt = 0                     # number of SET extensions (number of resets)
    check_idx = 0
    idx = 0

    while case < 5:
        current_V = 0
        current_H = 0

        if idx == 0:
            count_size = compute_count_size(mapping_info, result_Sequence[check_idx])
            init_VH = {}
            count_size2 = count_size.copy()

        # try folding (when V/H in area_VH are not all 0)
        if not all(v == 0 for v in area_VH.values()):
            # split V and H
            V_dict_temp = {k: v for k, v in area_VH.items() if k[0] == "V"}
            H_dict_temp = {k: v for k, v in area_VH.items() if k[0] == "H"}

            # sort (descending)
            sorted_V_list = sorted(V_dict_temp.items(), key=lambda item: int(item[1]), reverse=True)
            sorted_H_list = sorted(H_dict_temp.items(), key=lambda item: int(item[1]), reverse=True)

            # try folding (HNOR in parallel + VNOR in sequence)
            area_VH = Folding_This(sorted_V_list, crossbar, count_size2, area_VH)

            if area_VH is None:
                # cannot fold any further → stop condition
                if len(sorted_V_list) <= 1 or sorted_V_list[1][1] == 0:
                    if len(sorted_H_list) <= 1 or sorted_H_list[1][1] == 0:
                        # only one V and one H left
                        init_VH.update(V_dict_temp)
                        init_VH.update(H_dict_temp)
                        return None, init_VH
                    else:
                        return None, sorted_H_list[0][0]  # return the largest H
                else:
                    return None, sorted_V_list[0][0]      # return the largest V
            
            # folding succeeded → compute the current H/V size
            for n in range(4):   # HNOR in parallel
                current_H += int(area_VH["H" + str(n+2)])

            for nn in range(lut_max_V-1):   # VNOR in sequence
                current_V += int(area_VH["V" + str(nn+2)])

            # check that it fits within the crossbar grid
            if current_V <= len(crossbar.grid) and current_H <= len(crossbar.grid[0]):
                case = 7
                continue

    return area_VH


@timer
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
        # check that every count is below 5
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
                        real_folding_H = ccccount #H including earlier folding


                if len(sorted_V_difference) == 1:
                    if (int (value) % len(crossbar.grid)) == 0:
                        k = int(int (value) // len(crossbar.grid)) # k: how many times to fold, e.g. 2 places it in two parts
                    else: 
                        k = int(int (value) // len(crossbar.grid)) +1
                else:
                    if len(sorted_V_difference) != 1: 
                        k = int(real_folding_H / folding_H) if folding_H else 0  # avoid ZeroDivisionError
                        k+=1 #with several V sizes, increase the folding factor by one

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
                    #max V (height) after folding
                        if (V_max_i % k) != 0: V_max_div = (V_max_i*(k-1) // k) + 1
                        else: V_max_div = V_max_i*(k-1) // k

                        if (V_max_div) % (int(temp_V[1])) == 0:
                            V_max = V_max_div
                        else:
                            V_max = (V_max_div) + (int(temp_V[1]) - (V_max_div) % (int(temp_V[1]))) 

                        #2. whether it saves cycles compared to before
                        if k + V_max > k-1 + V_max_i: return area_VH,None

                        #check that the height after folding fits in the grid
                        if current_V3 + V_max > int(len(crossbar.grid)):
                            k+=1
                            i+=1
                            if i == 1 and len(sorted_V_difference) == 1: return None, None
                        else: break

                        #check that the width after folding fits in the grid 
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
                            if sum_H + folding_H < int(last_c): #fold only while staying within the IR limit
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                else:  #not folded yet
                    while True: 
                    #max V (height) after folding
                        if (V_max_i % k) != 0: V_max_div = (V_max_i // k) + 1
                        else: V_max_div = V_max_i // k

                        if (V_max_div) % (int(temp_V[1])) == 0:
                            V_max = V_max_div
                        else:
                            V_max = V_max_div + (int(temp_V[1]) - (V_max_div) % (int(temp_V[1]))) 

                        #2. whether it saves cycles compared to before
                        if k + V_max > k-1 + V_max_i: return area_VH, None

                        #check that the height after folding fits in the grid
                        if current_V3 + V_max > int(len(crossbar.grid)):
                            k+=1
                            i+=1
                            if i == 1 and len(sorted_V_difference) == 1: return None, None
                        else: break

                        #check that the width after folding fits in the grid 
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
                            if sum_H + folding_H < int(last_c): #fold only while staying within the IR limit
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None

                #check that the width after folding fits in the grid 
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
                    if sum_H + folding_H < int(last_c): #fold only while staying within the IR limit
                        area_VH["H"+str(folding_H)] = folding_H*k
                    else: return None, None
               
                # after folding, also consider the second-largest V   
                if len(V_list) > 1: #two or more H sizes
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

                keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # heights
                keys2 = [f"H{h}" for h in H]                   #count_VH = {key: 0 for key in keys}

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
                        area_VH[area_H] = int(max(temp_V)) #actual occupied area, considering the V size

                print(count_size2)
                     
                current_V = 0
                current_H = 0

                for n in range(4):
                    current_H += int(area_VH["H" + str(n+2)])

                for nn in range(lut_max_V-1):
                    current_V += int(area_VH["V" + str(nn+2)])

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

                #2. can it be folded further? 
                if new_sorted_V_list[0][1] + current_H > last_c:
                    return area_VH, count_size2
                    
                if new_sorted_V_list != sorted_V_list:
                    sorted_V_list = new_sorted_V_list
                    break




@timer
def Blocking(mapping_info, crossbar):
    seq = init_SET(mapping_info)
    count_size = compute_count_size(mapping_info, seq)
    print (count_size)
    init_area_VH = define_dict( count_size, crossbar)
    #result_area_VH = case_operate(seq, crossbar, mapping_info, init_area_VH)

    if None in init_area_VH:
        return init_area_VH[0], init_area_VH[1]
    return init_area_VH

#######NOR execution####### 
#@timer
def execute_NOR(current_level, luts, crossbar):
    global Attempt
    global Cycle
    global Path
    global VNOR_Cycle
    global HNOR_Cycle
    global current_level_luts

    past_row_cells = []

    file_path = os.path.join(Path, "instructions.txt")

    rows = len(crossbar.grid)
    cols = len(crossbar.grid[0])

    for current_level_lut in current_level_luts:
        row_cells = []
        col_cells = []
        past_row_cells2 = []
    # ---- HNOR (row-wise NOR) ----
        Hdone = False
        for r in range(rows):
            # collect the current-level LUT cells in this row
            if Hdone == True: break
            for c in range(cols):
                if crossbar.grid[r][c] is not None and crossbar.grid[r][c].split("_")[0] == current_level_lut.name:
                    if c not in past_row_cells:
                        if c not in row_cells:
                            row_cells.append(c)


                if current_level_lut.size[0] == len(row_cells) and past_row_cells2 != row_cells:  # HNOR can run with 2 or more cells
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

        # ---- VNOR (column-wise NOR) ----
    # VNORs are issued after every HNOR of the level (a VNOR acts on all columns of its rows)
    for current_level_lut in current_level_luts:
        col_cells = []
        Vdone = False
        for c in range(cols):
            if Vdone == True: break
            for r in range(rows):
                if crossbar.grid[r][c] is not None and "_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[0] == current_level_lut.name:
                    if r not in col_cells:
                        col_cells.append(r)

                if current_level_lut.size[1] == len(col_cells):  # VNOR can run with 2 or more cells
                    with open(file_path, 'a', encoding='utf-8') as f:
                        data = f"C{Cycle} VNOR  col:{c}, row:{col_cells[:-1]} -> {col_cells[-1]}\n"
                        f.write(data)
                    Cycle += 1
                    VNOR_Cycle += 1
                    col_cells = []
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

        for k, v in area_VH[1].items(): ##LEVEL UP logic when only one V is left
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

        one_V_luts_sorted = dict(sorted(one_V_luts.items(), key=lambda item: item[1].fanlevel))

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
    
    num = int(area_VH[1][1])
    if area_VH[1][1] == "V" and int(num) > lut_max_V :return False

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
            insert_info[int(num)-2] = [_a + _b for _a, _b in zip(insert_info[int(num)-2], mapping_info[current_level-1][int(num)-2])]
            mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
            if lut_level_up == 1: mapping_info.insert(current_level,insert_info)

    if area_VH[1][1] == "V":
        insert_info[int(num)-2] = [_a + _b for _a, _b in zip(insert_info[int(num)-2], mapping_info[current_level-1][int(num)-2])]
        mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
        if lut_level_up == 1: mapping_info.insert(current_level,insert_info)
    else: #H
        for i in range(lut_max_V):
            insert_info[i][int(num)-2] += mapping_info[current_level-1][i][int(num)-2]
            mapping_info[current_level-1][i][int(num)-2] = 0
        if lut_level_up == 1: mapping_info.insert(current_level,insert_info)



    return mapping_info


########################
@timer
def map_to_crossbar(luts, crossbar, mapping_info):
        global Copy_IR_Cycle
        global Reset_Cycle
        global VNOR_Cycle
        global HNOR_Cycle
        global A_star_Cycle
        global Place_Cycle
        global place_info_dict


        ordered_luts = sorted(luts.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
        
        @timer
        def reset_crossbar_except_last_col(target_level):   #target_level : level of the LUT to be mapped - 1 
            reset_c=[]
            reset_IR_list = []
            global Attempt
            global Cycle
            global Path
            global Copy_IR_Cycle
            global Reset_Cycle
            global lut_last_col
            global current_level_luts

            for r in range(len(crossbar.grid)):
                for c in range(len(crossbar.grid[0])-1, -1, -1):
                    if not crossbar.grid[r][c]: cell_content=None
                    elif crossbar.grid[r][c] and '_' in crossbar.grid[r][c]:      #mapped LUT
                        cell_content = crossbar.grid[r][c].split('_')[0]
                        if cell_content is not None and cell_content[0] =='~': 
                            cell_content = cell_content[1:]
                    elif crossbar.grid[r][c] and '_' not in crossbar.grid[r][c]:  #reset cells left by A*
                        crossbar.grid[r][c] = None
                        cell_content = None
                        if c not in reset_c:
                            reset_c.append(c)                                                                             ###cell_content defined. A* reset
                    
                    if cell_content and '_' in crossbar.grid[r][c] :
                        #cell_content2 = crossbar.grid[r][c].split('_')[1]
                        #if cell_content2 != "0" and cell_content2[0] == "~": cell_content2 = cell_content2[1:]
                        if c != luts[cell_content].col[-1]: 
                                    crossbar.grid[r][c] = None
                                    if c not in reset_c:
                                       reset_c.append(c)


            
            #for (r,c) in reset_IR_list:
            #filtered_list = [(fr, fc) for (r, c) in reset_IR_list if c <= int(len(crossbar.grid[0]) * 0.75)]
            """
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
            """

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

        def map_lut_with_reset(lut, luts, area_VH):      #copy (A*), then reset
            print(f"Trying to map LUT at level {lut.level} with fanin {lut.fanin} and expression {lut.expr}")
            print(f"Trying to use area: {area_VH}")
            print(f"Product terms: {lut.product_terms}")
            print(f"Number of terms: {lut.product_terms}")

            if not crossbar.map_lut(lut, luts, area_VH):
                execute_Copy(luts, crossbar) 
                execute_NOR(lut.level, luts, crossbar)  # evaluate the LUTs placed so far before the reset
                current_level_luts.clear()
                reset_crossbar_except_last_col(lut.level-1)
                if not crossbar.map_lut(lut, luts, area_VH):
                    print(f"[FATAL] LUT {lut.name} does not fit even after the reset")
                    sys.exit()
                return True
            return True
        # also print the state before and after the reset

        i = 0
        modified = False
        done = False
        current_level = None
        level_up_list = []
        lut_level_up = 0
        level_up = False
        used_luts = []
        
        global lut_last_col
        global current_level_luts
        global current_level_luts2

        current_level_luts2 = []
        current_level_luts = []

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
                                break
                        
                        if modified == True: break

                        if lut.level != current_level:
                        #or (lut.level == current_level and level_up == True):
                            if current_level > 1: lut_last_col = []
                            if modified == True: break
                            #if level_up == False: 
                            current_level = lut.level
                            if None not in area_VH:
                                past_area_VH = area_VH

                            for current_lut in current_level_luts2:
                                if current_lut.col[-1] not in lut_last_col:
                                    lut_last_col.append(current_lut.col[-1])

                            
                            print(f"\nFinished mapping level: {current_level-1}\n")
                            #  input("Press Enter to continue...")  
                            pass  # crossbar dump omitted (log size)
                            print('\n'+'\n')
                            crossbar = execute_Copy(luts, crossbar)
                            pass  # crossbar dump omitted (log size)
                            print('\n'+'\n')
                            print(current_level_luts)
                            execute_NOR(current_level -1, luts, crossbar)#, past_area_VH)
                            current_level_luts = []
                            reset_crossbar_except_last_col(lut.level-1)
                            pass  # crossbar dump omitted (log size)
                            print('\n'+'\n')      
                            print_runtime_summary()
                            print_section_summary()
                            #current_level += 1
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

                        if not map_lut_with_reset(lut, luts, area_VH): ##placement happens here
                            print("Mapping failed even after resetting!")
                            break   

                        used_luts.append(lut)
                        
                        if lut.level == current_level:
                            if lut not in current_level_luts:
                                current_level_luts.append(lut)
                            if lut not in current_level_luts2:
                                current_level_luts2.append(lut)
                     
                        if lut == ordered_luts[-1]:
                            current_level=lut.level
                            print("\nFinished mapping the last level\n")
                            crossbar = execute_Copy(luts, crossbar)
                            pass  # crossbar dump omitted (log size)
                            execute_NOR(current_level - 1, luts, crossbar)#, past_area_VH)
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
                            #invert the final outputs 

                            print_runtime_summary()
                            print_section_summary()
                    

                        if current_level == ordered_luts[0].level and ordered_luts[1].level == ordered_luts[0].level+1: 
                            del ordered_luts[0]
                            break   
                        


        temp_level_luts = ''.join(str(ordered_luts)+'\n')
        print('\n' + '\n' + '\n')
        print('-'*32)
        print("@@@@@@@@LUTS sorted by level")
        
        print(temp_level_luts)
    
        return crossbar


def print_runtime_summary():
    print("\n[ Function runtime summary ]")
    total_all = sum(data["total_time"] for data in runtimes.values())
    print(f"Total accumulated runtime: {total_all:.6f}s\n")
    for name, data in runtimes.items():
        count = data["count"]
        total = data["total_time"]
        avg = total / count if count else 0.0
        print(f"{name} calls {count} | total {total:.6f}s | avg {avg:.9f}s")

def print_section_summary():
    print("\n[ Section runtime summary ]")
    for name, times in section_times.items():
        count = len(times)
        total = sum(times)
        avg = total / count if count else 0.0
        print(f"{name} | calls {count} | total {total:.6f}s | avg {avg:.9f}s")




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
verilog_text2 = update_lut_info(verilog_text1)
print(verilog_text2)  
mapping_info = Optimizing_luts(verilog_text2)
print("-"*444)
#print(mapping_info)
crossbar_instance = initialize_crossbar(_cb_rows, _cb_cols)
verilog_crossbar3 = map_to_crossbar(verilog_text2, crossbar_instance, mapping_info)