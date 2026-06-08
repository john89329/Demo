"""Base classes for the Python Learning System.

Extensible design: to add a new course, create a folder under courses/,
implement a register_course() function that returns a Course instance.
The engine auto-discovers all courses.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional


# ── Enums ────────────────────────────────────────────────────────────────────

class ExerciseType:
    CODE_OUTPUT = "code_output"      # write code, match stdout
    MULTIPLE_CHOICE = "multiple_choice"  # pick an answer
    FILL_BLANK = "fill_blank"        # fill in missing code
    FREE_PRACTICE = "free_practice"  # open-ended, show reference answer


class Difficulty:
    BEGINNER = "入门"
    EASY = "初级"
    MEDIUM = "中级"
    HARD = "高级"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Exercise:
    """A single practice exercise within a lesson."""
    instruction: str                         # what the user should do
    exercise_type: str                       # one of ExerciseType.*
    hint: str = ""                           # hint text (shown on request)
    # For MULTIPLE_CHOICE:
    choices: Optional[list[str]] = None      # list of option strings
    correct_answer: Optional[str] = None     # the correct option letter or value
    # For CODE_OUTPUT / FILL_BLANK:
    expected_output: Optional[str] = None    # expected stdout after execution
    setup_code: Optional[str] = None         # code to run before user code
    reference_answer: Optional[str] = None   # shown for FREE_PRACTICE
    # For FILL_BLANK:
    blank_marker: str = "___"                # placeholder in template
    template: Optional[str] = None           # code template with blanks


@dataclass
class Lesson:
    """A single lesson within a course."""
    lesson_id: str                           # unique id e.g. "00_setup"
    title: str                               # display title
    duration: str                            # e.g. "15分钟"
    content: str                             # main teaching text (markdown-like)
    exercises: list[Exercise] = field(default_factory=list)

    def get_exercise_count(self) -> int:
        return len(self.exercises)


@dataclass
class Course:
    """A complete course made of ordered lessons."""
    course_id: str                           # unique folder name / id
    name: str                                # display name
    description: str                         # short intro
    difficulty: str                          # from Difficulty
    lessons: list[Lesson] = field(default_factory=list)

    def get_total_lessons(self) -> int:
        return len(self.lessons)
