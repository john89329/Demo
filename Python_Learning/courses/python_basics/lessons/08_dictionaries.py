"""Lesson 08: Dictionaries — key-value pairs."""

from courses.base import Lesson, Exercise, ExerciseType


def create_lesson() -> Lesson:
    return Lesson(
        lesson_id="08_dicts",
        title="第8课：字典 — 键值对存储",
        duration="30分钟",
        content="""
## 什么是字典 (dict)？

字典是**键值对 (key-value)** 的集合，用 `{}` 表示。像一本真正的字典——你通过"词"（键）来查"释义"（值）。

```python
student = {
    "name": "小明",
    "age": 20,
    "scores": [85, 90, 78],
    "is_student": True
}
```

---

### 访问值

```python
student["name"]              # "小明" — 直接访问
student.get("age")           # 20     — 用 get 方法
student.get("gender", "未知")  # "未知" — 键不存在时返回默认值
```

**区别：** `[]` 访问不存在的键会报错，`.get()` 返回 None 或默认值。

---

### 增、改、删

```python
# 添加
student["gender"] = "男"

# 修改
student["age"] = 21

# 删除
del student["is_student"]
age = student.pop("age")     # 删除并返回值
```

---

### 常用方法

```python
d = {"a": 1, "b": 2, "c": 3}

d.keys()        # dict_keys(['a', 'b', 'c'])    所有键
d.values()      # dict_values([1, 2, 3])        所有值
d.items()       # dict_items([('a',1), ...])    键值对元组

"a" in d        # True   检查键是否存在
len(d)          # 3      键值对数量
```

---

### 遍历字典

```python
# 遍历键
for key in d:
    print(key, d[key])

# 同时遍历键和值（推荐）
for key, value in d.items():
    print(f"{key}: {value}")
```

---

### 字典的应用场景

```python
# 1. 统计字符出现次数
text = "hello"
counts = {}
for ch in text:
    counts[ch] = counts.get(ch, 0) + 1
print(counts)  # {'h': 1, 'e': 1, 'l': 2, 'o': 1}

# 2. 存储配置/设置
config = {"theme": "dark", "lang": "zh", "font_size": 14}
```
""",
        exercises=[
            Exercise(
                instruction="创建一个字典 info 包含你的名字、年龄、喜欢的编程语言，然后用 for 循环遍历打印每个键值对。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="for k, v in info.items(): print(f'{k}: {v}')",
                expected_output="",
                reference_answer='info = {"name": "小明", "age": 20, "language": "Python"}\nfor k, v in info.items():\n    print(f"{k}: {v}")',
            ),
            Exercise(
                instruction="写一段代码统计 'hello world' 中每个字符出现的次数（忽略空格），用字典存储。",
                exercise_type=ExerciseType.CODE_OUTPUT,
                hint="遍历字符串，counts[ch] = counts.get(ch, 0) + 1",
                expected_output="",
                reference_answer='text = "hello world"\ncounts = {}\nfor ch in text:\n    if ch != " ":\n        counts[ch] = counts.get(ch, 0) + 1\nprint(counts)',
            ),
            Exercise(
                instruction="选择题：dict.get('key', 'default') 的作用是？",
                exercise_type=ExerciseType.MULTIPLE_CHOICE,
                choices=[
                    "A. 获取'key'的值，如果不存在就设置为'default'并返回",
                    "B. 获取'key'的值，如果不存在就返回'default'（不修改原字典）",
                    "C. 总是返回'default'",
                    "D. 如果'key'存在就返回'default'",
                ],
                correct_answer="B",
            ),
        ],
    )
