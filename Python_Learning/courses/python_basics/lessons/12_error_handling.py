"""Lesson 12: Error Handling — dealing with exceptions gracefully."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="12_errors",
        title="第12课：错误处理 — 程序崩溃也不怕",
        duration="25分钟",
        content="""
## 程序为什么会崩溃？

当 Python 遇到无法处理的情况时，它会**抛出异常 (raise an exception)**。

```python
num = int("abc")       # ValueError: invalid literal for int()
x = 10 / 0             # ZeroDivisionError
lst = [1, 2, 3]
print(lst[5])          # IndexError
d = {"a": 1}
print(d["b"])          # KeyError
```

---

### try / except — 捕获异常

```python
try:
    num = int(input("输入一个数字: "))
    print(f"100 / {num} = {100 / num}")
except ValueError:
    print("请输入有效的数字！")
except ZeroDivisionError:
    print("不能除以零哦！")
```

---

### 完整的异常处理结构

```python
try:
    # 可能出错的代码
    result = 10 / int(input("输入数字: "))
except ValueError:
    print("类型错误")
except ZeroDivisionError:
    print("除以零")
except Exception as e:
    print(f"其他错误: {e}")
else:
    print(f"计算结果: {result}")   # 没有异常时执行
finally:
    print("程序结束")              # 无论是否有异常都执行
```

---

### 常见异常类型

| 异常 | 触发条件 |
|------|---------|
| `ValueError` | 值类型正确但不合理（如 int("abc")） |
| `TypeError` | 类型错误（如 "a" + 1） |
| `ZeroDivisionError` | 除以零 |
| `IndexError` | 列表索引越界 |
| `KeyError` | 字典键不存在 |
| `FileNotFoundError` | 文件不存在 |
| `NameError` | 变量未定义 |
| `SyntaxError` | 语法错误（代码写错了） |

---

### 主动抛出异常

```python
def divide(a, b):
    if b == 0:
        raise ValueError("除数不能为零")
    return a / b
```

---

### 最佳实践

- **捕获具体的异常类型**，不要只用 `except:` 裸捕获
- **不要过度使用 try/except** — 不是所有地方都需要捕获
- 异常处理是**用户的良好体验**，不是掩盖 bug
""",
        exercises=[
            Exercise(
                instruction="写一段代码：让用户输入一个数字，然后计算 100 除以它。用 try/except 处理非数字输入和除以零的情况。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="try...except ValueError...except ZeroDivisionError",
                expected_output="",
                reference_answer='try:\n    num = int(input("输入一个数字: "))\n    print(f"100 / {num} = {100 / num}")\nexcept ValueError:\n    print("请输入有效的数字！")\nexcept ZeroDivisionError:\n    print("不能除以零！")',
            ),
            Exercise(
                instruction="选择题：以下代码输出什么？\n\ntry:\n    print(1/0)\nexcept ValueError:\n    print('A')\nexcept ZeroDivisionError:\n    print('B')\nfinally:\n    print('C')",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. A 然后 C",
                    "B. B 然后 C",
                    "C. 只有 C",
                    "D. 报错",
                ],
                correct_answer="B",
            ),
            Exercise(
                instruction="自由练习：写一个函数 safe_get(lst, index)，如果索引越界返回 None 而不是崩溃。",
                exercise_type=ExerciseType.FREE_PRACTICE,
                hint="try: return lst[index]; except IndexError: return None",
                reference_answer="def safe_get(lst, index):\n    try:\n        return lst[index]\n    except IndexError:\n        return None",
            ),
        ],
    )
