"""Lesson 09: Tuples and Sets."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="09_tuples_sets",
        title="第9课：元组与集合",
        duration="25分钟",
        content="""
## 元组 (tuple) — 不可变的列表

元组用 `()` 表示，一旦创建**不能修改**。

```python
point = (3, 4)
names = ("小明", "小红", "小刚")
single = (1,)          # 单元素元组，必须加逗号！
empty = ()             # 空元组
```

### 元组的用途

```python
# 1. 返回多个值
def get_info():
    return ("小明", 20, "男")

name, age, gender = get_info()   # 解包

# 2. 作为字典的键（列表不能做键！）
locations = {(39.9, 116.4): "北京", (31.2, 121.5): "上海"}

# 3. 保护数据不被意外修改
DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
```

### 元组和列表的对比

| 特性 | 列表 (list) | 元组 (tuple) |
|------|------------|-------------|
| 创建 | `[1, 2, 3]` | `(1, 2, 3)` |
| 可变 | ✓ 可以增删改 | ✗ 不可变 |
| 速度 | 较慢 | 较快 |
| 作字典键 | ✗ | ✓ |

---

## 集合 (set) — 无序、不重复

集合用 `{}` 表示（但没有键值对，注意和字典区分）。

```python
nums = {1, 2, 2, 3, 3, 3}
print(nums)            # {1, 2, 3} — 自动去重！

empty_set = set()      # 空集合（不能用 {}，那是空字典）
```

### 集合运算

```python
a = {1, 2, 3, 4}
b = {3, 4, 5, 6}

a & b        # {3, 4}      交集
a | b        # {1,2,3,4,5,6}  并集
a - b        # {1, 2}      差集
a ^ b        # {1,2,5,6}   对称差（在一边但不同时在两边）
```

### 集合常用方法

```python
s = {1, 2, 3}
s.add(4)           # {1, 2, 3, 4}
s.remove(2)        # {1, 3, 4}  — 元素不存在会报错
s.discard(5)       # 安全删除，不存在也不报错
3 in s             # True       — 非常快！O(1)

# 去重利器
nums = [1, 2, 2, 3, 3, 3]
unique = list(set(nums))    # [1, 2, 3]
```
""",
        exercises=[
            Exercise(
                instruction="创建元组 ('苹果', '香蕉', '橘子')，用解包语法分别赋值给三个变量并打印。然后尝试修改元组中的一个元素，看会发生什么。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="a, b, c = ('苹果', '香蕉', '橘子')",
                expected_output="",
                reference_answer='fruits = ("苹果", "香蕉", "橘子")\na, b, c = fruits\nprint(a, b, c)',
            ),
            Exercise(
                instruction="有两个集合 {1, 2, 3, 4} 和 {3, 4, 5, 6}，分别打印它们的交集、并集、差集（第一个减第二个）。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="&, |, -",
                expected_output="",
                reference_answer='a = {1, 2, 3, 4}\nb = {3, 4, 5, 6}\nprint(a & b)\nprint(a | b)\nprint(a - b)',
            ),
            Exercise(
                instruction="选择题：哪个语句会创建一个空集合？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. s = {}",
                    "B. s = set()",
                    "C. s = ()",
                    "D. s = []",
                ],
                correct_answer="B",
            ),
        ],
    )
