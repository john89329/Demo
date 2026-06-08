"""Lesson 03: Numbers and math operations."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="03_numbers",
        title="第3课：数字运算",
        duration="25分钟",
        content="""
## Python 中的数字类型

Python 有三种数字类型：

| 类型 | 示例 | 说明 |
|------|------|------|
| `int` | `42`, `-7`, `0` | 整数，无限大 |
| `float` | `3.14`, `-0.5`, `1e3` | 浮点数 |
| `complex` | `1+2j` | 复数（暂时不常用） |

---

### 基本运算符

```python
10 + 3    # 13  加法
10 - 3    # 7   减法
10 * 3    # 30  乘法
10 / 3    # 3.333...  除法（总是返回 float）
10 // 3   # 3   整除（向下取整）
10 % 3    # 1   取余（模运算）
10 ** 3   # 1000  幂运算（10的3次方）
```

---

### 运算优先级

从高到低：
1. `()` 括号
2. `**` 幂
3. `* / // %` 乘除取余
4. `+ -` 加减

**有疑问就加括号！**

```python
2 + 3 * 4     # 14   （不是 20）
(2 + 3) * 4   # 20
```

---

### 实用数学函数

```python
abs(-5)        # 5          绝对值
round(3.14159, 2)  # 3.14  四舍五入到2位
pow(2, 10)     # 1024       等价于 2 ** 10
max(1, 5, 3)   # 5          最大值
min(1, 5, 3)   # 1          最小值
sum([1, 2, 3]) # 6          求和
```

---

### 类型转换注意事项

```python
int("42")      # 42        ✓ 可以
int("3.14")    # 报错！     ✗ 不能直接转带小数点的
int(float("3.14"))  # 3    ✓ 先转 float 再转 int
float(42)      # 42.0      ✓ int 可以转 float
```
""",
        exercises=[
            Exercise(
                instruction="计算并打印：12345 + 67890 和 2的10次方",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="print(12345 + 67890) 和 print(2 ** 10)",
                expected_output="",
                reference_answer="print(12345 + 67890)\nprint(2 ** 10)",
            ),
            Exercise(
                instruction="计算 100 除以 3 的结果，分别打印：精确除法、整除、余数",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="100/3, 100//3, 100%3",
                expected_output="",
                reference_answer="print(100 / 3)\nprint(100 // 3)\nprint(100 % 3)",
            ),
            Exercise(
                instruction="选择题：10 // 3 * 2 的结果是？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 6.666...",
                    "B. 6",
                    "C. 1",
                    "D. 5",
                ],
                correct_answer="B",
            ),
        ],
    )
