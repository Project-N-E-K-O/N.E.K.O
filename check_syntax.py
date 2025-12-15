import ast
import sys

try:
    with open('d:/STUFF/WORK/N.E.K.O/main_routers/workshop_router.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    ast.parse(content)
    print("语法检查通过！")
except SyntaxError as e:
    print(f"语法错误在第 {e.lineno} 行，第 {e.offset} 列: {e.msg}")
    print("上下文:")
    lines = content.split('\n')
    start = max(0, e.lineno-5)
    end = min(len(lines), e.lineno+5)
    for i in range(start, end):
        line = lines[i]
        marker = "  ^" if i == e.lineno-1 else "   "
        print(f'{i+1:4d}: {line}')
        if i == e.lineno-1:
            print(f"      {marker:>{e.offset}}")
