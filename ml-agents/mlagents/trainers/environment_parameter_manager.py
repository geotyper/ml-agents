from typing import Dict, List, Tuple
import numpy as np
import math
from mlagents.trainers.settings import (
    EnvironmentParameterSettings,
    CompletionCriteriaSettings,
    ParameterRandomizationSettings,
)
from mlagents.trainers.training_status import GlobalTrainingStatus, StatusType

from mlagents_envs.logging_util import get_logger

logger = get_logger(__name__)


class EnvironmentParameterManager:
    def __init__(
        self,
        settings: Dict[str, EnvironmentParameterSettings],
        run_seed: int,
        restore: bool,
    ):
        self._dict_settings = settings
        for parameter_name in self._dict_settings.keys():
            initial_lesson = GlobalTrainingStatus.get_parameter_state(
                parameter_name, StatusType.LESSON_NUM
            )
            if initial_lesson is None or not restore:
                GlobalTrainingStatus.set_parameter_state(
                    parameter_name, StatusType.LESSON_NUM, 0
                )
        self._smoothing_values: Dict[str, float] = {}
        for key in self._dict_settings.keys():
            self._smoothing_values[key] = 0.0
        # Update the seeds of the samplers
        self._set_sampler_seeds(run_seed)

    def _set_sampler_seeds(self, seed):
        offset = 0
        for settings in self._dict_settings.values():
            for lesson in settings.lessons:
                if lesson.sampler.seed == -1:
                    lesson.sampler.seed = seed + offset
                    offset += 1

    def get_minimum_reward_buffer_size(self, behavior_name: str) -> int:
        result = 1
        for settings in self._dict_settings.values():
            for lesson in settings.lessons:
                if lesson.completion_criteria is not None:
                    if lesson.completion_criteria.behavior == behavior_name:
                        result = max(
                            result, lesson.completion_criteria.min_lesson_length
                        )
        return result

    def get_current_samplers(self) -> Dict[str, ParameterRandomizationSettings]:
        samplers: Dict[str, ParameterRandomizationSettings] = {}
        for param_name, settings in self._dict_settings.items():
            lesson_num = GlobalTrainingStatus.get_parameter_state(
                param_name, StatusType.LESSON_NUM
            )
            lesson = settings.lessons[lesson_num]
            samplers[param_name] = lesson.sampler
        return samplers

    def get_current_lesson_number(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for parameter_name in self._dict_settings.keys():
            result[parameter_name] = GlobalTrainingStatus.get_parameter_state(
                parameter_name, StatusType.LESSON_NUM
            )
        return result

    def update_lessons(
        self,
        trainer_steps: Dict[str, int],
        trainer_max_steps: Dict[str, int],
        trainer_reward_buffer: Dict[str, List[float]],
    ) -> Tuple[bool, bool]:
        must_reset = False
        updated = False
        for param_name, settings in self._dict_settings.items():
            lesson_num = GlobalTrainingStatus.get_parameter_state(
                param_name, StatusType.LESSON_NUM
            )
            lesson = settings.lessons[lesson_num]
            if (
                lesson.completion_criteria is not None
                and len(settings.lessons) > lesson_num
            ):
                behavior_to_consider = lesson.completion_criteria.behavior
                must_increment, new_smoothing = self._need_increment(
                    lesson.completion_criteria,
                    float(trainer_steps[behavior_to_consider])
                    / float(trainer_max_steps[behavior_to_consider]),
                    trainer_reward_buffer[behavior_to_consider],
                    self._smoothing_values[param_name],
                )
                self._smoothing_values[param_name] = new_smoothing
                if must_increment:
                    GlobalTrainingStatus.set_parameter_state(
                        param_name, StatusType.LESSON_NUM, lesson_num + 1
                    )
                    logger.info(
                        f"Parameter '{param_name}' has changed. Now in lesson '{settings.lessons[lesson_num+1].name}'"
                    )
                    updated = True
                    if lesson.completion_criteria.require_reset:
                        must_reset = True
        return updated, must_reset

    @staticmethod
    def _need_increment(
        increment_condition: CompletionCriteriaSettings,
        progress: float,
        reward_buffer: List[float],
        smoothing: float,
    ) -> Tuple[bool, float]:
        # Is the min number of episodes reached
        if len(reward_buffer) < increment_condition.min_lesson_length:
            return False, smoothing
        if (
            increment_condition.measure
            == CompletionCriteriaSettings.MeasureType.PROGRESS
        ):
            if progress > increment_condition.threshold:
                return True, smoothing
        if increment_condition.measure == CompletionCriteriaSettings.MeasureType.REWARD:
            if len(reward_buffer) < 1:
                return False, smoothing
            measure = np.mean(reward_buffer)
            if math.isnan(measure):
                return False, smoothing
            if increment_condition.signal_smoothing:
                measure = 0.25 * smoothing + 0.75 * measure
                smoothing = measure
            if measure > increment_condition.threshold:
                return True, smoothing
        return False, smoothing