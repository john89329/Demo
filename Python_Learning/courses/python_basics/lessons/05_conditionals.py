"""Lesson 05: Conditionals — making decisions in code."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="05_conditional",
        title="第5课：条件判断 — 让程序做决策",
        duration="30分钟",
        content="""
## if / elif / else

程序不是只能从上到下执行——它可以**根据不同情况走不同分支**。

```python
score = 85

if score >= 90:
    print("优秀")
elif score >= 80:
    print("良好")
elif score >= 60:
    print("及格")
else:
    print("不及格")

# 输出: 良好
```

**结构：**
- `if` 后面跟一个条件（True/False），条件为真时执行缩进块
- `elif`（else if 的缩写）可多个，当前面的条件都不满足时检查
- `else` 在所有条件都不满足时执行

**注意 Python 的缩进！** 用 4 个空格（或 1 个 Tab），同一个代码块必须对齐。

---

### 比较运算符

| 运算符 | 含义 | 示例 |
|--------|------|------|
| `==` | 等于 | `x == 5` |
| `!=` | 不等于 | `x != 5` |
| `>` | 大于 | `x > 5` |
| `<` | 小于 | `x < 5` |
| `>=` | 大于等于 | `x >= 5` |
| `<=` | 小于等于 | `x <= 5` |

---

### 逻辑运算符

```python
# and: 两边都为 True 才为 True
age = 20
if age >= 18 and age <= 60:
    print("成年人")

# or: 任一边为 True 即为 True
day = "周六"
if day == "周六" or day == "周日":
    print("周末！")

# not: 取反
if not False:   # True
    print("取反了")
```

---

### 条件表达式（三元运算）

```python
result = "及格" if score >= 60 else "不及格"
```

---

### 嵌套 if

```python
if age >= 18:
    if has_id:
        print("可以进入")
    else:
        print("需要身份证")
else:
    print("未成年")
```
""",
        exercises=[
            Exercise(
                instruction="写一个程序：判断一个数字是正数、负数还是零。提示：输出格式为'正数'或'负数'或'零'",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="num = 5; if num > 0: print('正数') ...",
                expected_output="正数",
                reference_answer='num = 5\nif num > 0:\n    print("正数")\nelif num < 0:\n    print("负数")\nelse:\n    print("零")',
            ),
            Exercise(
                instruction="写一个程序：输入年龄，判断属于哪个年龄段：0-12(儿童)、13-17(青少年)、18-59(成人)、60+(老年)。测试 age=25",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="用 if/elif/else，注意条件顺序（从大到小或从小到大）",
                expected_output="",
                reference_answer='age = 25\nif age < 0:\n    print("无效年龄")\nelif age <= 12:\n    print("儿童")\nelif age <= 17:\n    print("青少年")\nelif age <= 59:\n    print("成人")\nelse:\n    print("老年")',
            ),
            Exercise(
                instruction="选择题：以下代码输出什么？\nx = 5\nif x > 3:\n    print('A')\nelif x > 4:\n    print('B')\nelse:\n    print('C')",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. A",
                    "B. B",
                    "C. C",
                    "D. A B",
                ],
                correct_answer="A",
            ),
        ],
    )
