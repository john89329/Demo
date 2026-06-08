"""Progress tracking — save/load learning progress to JSON."""

import json
import os
from datetime import datetime
from typing import Optional


class Progress:
    """Persistent progress tracker per course."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.filepath = os.path.join(self.data_dir, "progress.json")
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get_course_progress(self, course_id: str) -> dict:
        """Get progress for a specific course."""
        return self._data.get(course_id, {
            "started_at": None,
            "completed_lessons": [],
            "current_lesson": None,
            "scores": {},
            "completed": False,
        })

    def start_course(self, course_id: str):
        """Mark a course as started."""
        if course_id not in self._data:
            self._data[course_id] = {
                "started_at": datetime.now().isoformat(),
                "completed_lessons": [],
                "current_lesson": None,
                "scores": {},
                "completed": False,
            }
        else:
            self._data[course_id]["started_at"] = \
                self._data[course_id]["started_at"] or datetime.now().isoformat()
        self._save()

    def set_current_lesson(self, course_id: str, lesson_id: str):
        """Set which lesson the user is currently on."""
        if course_id not in self._data:
            self.start_course(course_id)
        self._data[course_id]["current_lesson"] = lesson_id
        self._save()

    def complete_lesson(
        self,
        course_id: str,
        lesson_id: str,
        score: Optional[dict] = None
    ):
        """Mark a lesson as completed with optional score."""
        if course_id not in self._data:
            self.start_course(course_id)
        course = self._data[course_id]
        if lesson_id not in course["completed_lessons"]:
            course["completed_lessons"].append(lesson_id)
        if score:
            course["scores"][lesson_id] = score
        self._save()

    def complete_course(self, course_id: str):
        """Mark an entire course as completed."""
        if course_id in self._data:
            self._data[course_id]["completed"] = True
            self._data[course_id]["completed_at"] = datetime.now().isoformat()
            self._save()

    def is_lesson_completed(self, course_id: str, lesson_id: str) -> bool:
        progress = self.get_course_progress(course_id)
        return lesson_id in progress.get("completed_lessons", [])

    def get_next_lesson_index(self, course_id: str, total_lessons: int) -> int:
        """Return index of next uncompleted lesson, or total_lessons if all done."""
        progress = self.get_course_progress(course_id)
        completed = set(progress.get("completed_lessons", []))
        # Map lesson indices
        for i in range(total_lessons):
            lesson_id = f"{i:02d}"  # simplified: assumes lesson_ids match index pattern
            if lesson_id not in completed:
                return i
        return total_lessons

    def reset_course(self, course_id: str):
        """Reset progress for a course."""
        if course_id in self._data:
            del self._data[course_id]
            self._save()
