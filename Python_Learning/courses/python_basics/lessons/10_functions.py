"""Lesson 10: Functions — reusable blocks of code."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="10_functions",
        title="第10课：函数 — 封装可复用的代码",
        duration="35分钟",
        content="""
## 什么是函数？

函数是一段**有名字的、可重复使用的代码块**。你给它输入（参数），它给你输出（返回值）。

---

### 定义和调用函数

```python
def greet(name):
    '''向某人打招呼'''      # 文档字符串 (docstring)
    return f"你好, {name}!"

# 调用
result = greet("小明")
print(result)            # 你好, 小明!
print(greet("小红"))      # 你好, 小红!
```

**def** = define（定义）
**return** = 返回结果给调用者

---

### 参数类型

```python
# 1. 默认参数
def greet(name, greeting="你好"):
    return f"{greeting}, {name}!"

greet("小明")                  # "你好, 小明!"
greet("小明", greeting="早上好")  # "早上好, 小明!"

# 2. 位置参数 vs 关键字参数
def describe(name, age, city):
    return f"{name}, {age}岁, 来自{city}"

describe("小明", 20, "北京")                    # 位置
describe(age=20, name="小明", city="北京")      # 关键字（顺序不重要）

# 3. *args — 可变数量的位置参数
def sum_all(*args):
    return sum(args)

sum_all(1, 2, 3, 4, 5)       # 15

# 4. **kwargs — 可变数量的关键字参数
def print_info(**kwargs):
    for k, v in kwargs.items():
        print(f"{k}: {v}")

print_info(name="小明", age=20)
```

---

### 返回值

```python
# 单一返回值
def add(a, b):
    return a + b

# 多个返回值（实际返回元组）
def get_min_max(lst):
    return min(lst), max(lst)

low, high = get_min_max([3, 1, 4, 1, 5])   # 解包
print(low, high)   # 1 5

# 无 return 时返回 None
def say_hello():
    print("hello")      # 只是打印，不返回任何值
```

---

### 变量作用域

```python
x = 10             # 全局变量

def my_func():
    y = 20         # 局部变量（只在函数内有效）
    return x + y   # 可以读取全局变量 x

# print(y)         # 报错！y 在函数外不存在
```

**规则：** 函数内可以读取全局变量，但不能直接修改（需要用 `global` 关键字声明）。

---

### 为什么要用函数？

1. **避免重复代码** — 写一次，用多次
2. **提高可读性** — 好名字让代码自解释
3. **方便修改** — 改一处，处处生效
4. **易于测试** — 可以单独测试每个函数
""",
        exercises=[
            Exercise(
                instruction="写一个函数 `is_even(n)`，判断一个数是否为偶数，返回 True/False。然后测试 n=10 和 n=7。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="return n % 2 == 0",
                expected_output="",
                reference_answer='def is_even(n):\n    return n % 2 == 0\n\nprint(is_even(10))\nprint(is_even(7))',
            ),
            Exercise(
                instruction="写一个函数 `grade(score)`：>=90 返回'A'，>=80 返回'B'，>=60 返回'C'，<60 返回'D'。测试 score=85。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="用 if/elif/else",
                expected_output="B",
                reference_answer='def grade(score):\n    if score >= 90:\n        return "A"\n    elif score >= 80:\n        return "B"\n    elif score >= 60:\n        return "C"\n    else:\n        return "D"\n\nprint(grade(85))',
            ),
            Exercise(
                instruction="选择题：以下函数返回什么？\n\ndef test():\n    print('Hello')\n\nresult = test()\nprint(result)",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 'Hello'",
                    "B. Hello 然后是 None",
                    "C. None",
                    "D. 报错",
                ],
                correct_answer="B",
            ),
        ],
    )
