"""Lesson 01: Variables and Data Types."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="01_variables",
        title="第1课：变量与数据类型",
        duration="25分钟",
        content="""
## 什么是变量？

变量就像一个**贴了标签的盒子**，你可以往里面放东西（数据），然后通过标签找到它。

```python
name = "小明"        # 把 "小明" 放进叫 name 的盒子里
age = 25             # 把 25 放进叫 age 的盒子里
height = 1.75        # 把 1.75 放进叫 height 的盒子里
```

和很多语言不同，Python 不需要提前声明变量类型——解释器会自动判断。

---

### Python 的基本数据类型

| 类型 | 关键字 | 示例 | 说明 |
|------|--------|------|------|
| 字符串 | `str` | `"hello"` | 用引号包裹的文本 |
| 整数 | `int` | `42` | 没有小数点的数字 |
| 浮点数 | `float` | `3.14` | 带小数点的数字 |
| 布尔值 | `bool` | `True / False` | 只有这两个值 |
| 空值 | `NoneType` | `None` | 表示"什么都没有" |

---

### 用 type() 查看类型

```python
print(type("hello"))   # <class 'str'>
print(type(42))        # <class 'int'>
print(type(3.14))      # <class 'float'>
print(type(True))      # <class 'bool'>
print(type(None))      # <class 'NoneType'>
```

---

### 变量命名规则

- 只能包含 **字母、数字、下划线**
- 不能以数字开头
- 区分大小写（`name` 和 `Name` 是不同的）
- 不能用 Python 关键字（如 `if`、`for`、`class`）
- 建议用有意义的名字，用下划线分隔：`user_name`、`total_score`

---

### 多重赋值

```python
x, y, z = 1, 2, 3        # 同时赋多个值
a = b = c = 0            # 三个变量都是 0
```

---

### 类型转换

```python
int("42")      # 字符串 → 整数: 42
str(42)        # 整数 → 字符串: "42"
float("3.14")  # 字符串 → 浮点数: 3.14
bool(0)        # 0 → False
bool(1)        # 非零 → True
```
""",
        exercises=[
            Exercise(
                instruction="创建一个变量 name 存储你的名字，一个变量 age 存储你的年龄，然后打印出来。输出格式：'我叫XXX，今年X岁'",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="用 f-string: f'我叫{name}，今年{age}岁'",
                expected_output="我叫小明，今年20岁",
                reference_answer='name = "小明"\nage = 20\nprint(f"我叫{name}，今年{age}岁")',
            ),
            Exercise(
                instruction="用 type() 查看以下值的类型：42, '42', 42.0, True",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="print(type(42))",
                expected_output="",
                reference_answer='print(type(42))\nprint(type("42"))\nprint(type(42.0))\nprint(type(True))',
            ),
            Exercise(
                instruction="选择题：以下哪个不是合法的 Python 变量名？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. my_var",
                    "B. _test",
                    "C. 2things",
                    "D. userName",
                ],
                correct_answer="C",
            ),
        ],
    )
