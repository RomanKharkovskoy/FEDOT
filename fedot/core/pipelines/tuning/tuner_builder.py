from datetime import timedelta
from typing import Type, Union, Iterable, Sequence

from golem.core.tuning.optuna_tuner import OptunaTuner
from golem.core.tuning.simultaneous import SimultaneousTuner
from golem.core.tuning.tuner_interface import BaseTuner
from golem.utilities.data_structures import ensure_wrapped_in_sequence

from fedot.core.constants import DEFAULT_TUNING_ITERATIONS_NUMBER
from fedot.core.data.data import InputData
from fedot.core.optimisers.objective import PipelineObjectiveEvaluate
from fedot.core.optimisers.objective.data_source_splitter import DataSourceSplitter
from fedot.core.optimisers.objective.metrics_objective import MetricsObjective
from fedot.core.pipelines.adapters import PipelineAdapter
from fedot.core.pipelines.pipeline_composer_requirements import PipelineComposerRequirements
from fedot.core.pipelines.tuning.search_space import PipelineSearchSpace
from fedot.core.repository.quality_metrics_repository import MetricType, MetricsEnum
from fedot.core.repository.tasks import Task
from fedot.utilities.define_metric_by_task import MetricByTask


class TunerBuilder:
    def __init__(self, task: Task):
        self.tuner_class = SimultaneousTuner
        self.cv_folds = None
        self.validation_blocks = None
        self.n_jobs = -1
        self.metric: Sequence[MetricsEnum] = MetricByTask.get_default_quality_metrics(task.task_type)
        self.iterations = DEFAULT_TUNING_ITERATIONS_NUMBER
        self.early_stopping_rounds = None
        self.timeout = timedelta(minutes=5)
        self.search_space = PipelineSearchSpace()
        self.eval_time_constraint = None
        self.additional_params = {}
        self.adapter = PipelineAdapter()

    def with_tuner(self, tuner: Type[BaseTuner]):
        self.tuner_class = tuner
        return self

    def with_requirements(self, requirements: PipelineComposerRequirements):
        self.cv_folds = requirements.cv_folds
        self.n_jobs = requirements.n_jobs
        return self

    def with_cv_folds(self, cv_folds: int):
        self.cv_folds = cv_folds
        return self

    def with_validation_blocks(self, validation_blocks: int):
        self.validation_blocks = validation_blocks
        return self

    def with_n_jobs(self, n_jobs: int):
        self.n_jobs = n_jobs
        return self

    def with_metric(self, metrics: Union[MetricType, Iterable[MetricType]]):
        self.metric = ensure_wrapped_in_sequence(metrics)
        return self

    def with_iterations(self, iterations: int):
        self.iterations = iterations
        return self

    def with_early_stopping_rounds(self, early_stopping_rounds: int):
        self.early_stopping_rounds = early_stopping_rounds
        return self

    def with_timeout(self, timeout: Union[timedelta, float]):
        if timeout in [-1, None]:
            self.timeout = None
        else:
            if isinstance(timeout, timedelta):
                self.timeout = timeout
            elif isinstance(timeout, float):
                self.timeout = timedelta(minutes=timeout)
        return self

    def with_eval_time_constraint(self, eval_time_constraint: Union[timedelta, int, float]):
        self.eval_time_constraint = eval_time_constraint
        return self

    def with_search_space(self, search_space: PipelineSearchSpace):
        self.search_space = search_space
        return self

    def with_adapter(self, adapter):
        self.adapter = adapter
        return self

    def with_additional_params(self, **parameters):
        self.additional_params.update(parameters)
        return self

    def build(self, data: InputData) -> BaseTuner:
        if len(self.metric) > 1:
            if self.tuner_class is OptunaTuner:
                self.additional_params.update({'objectives_number': len(self.metric)})
            else:
                raise ValueError('Multi objective tuning applicable only for OptunaTuner.')
        objective = MetricsObjective(self.metric, is_multi_objective=len(self.metric) > 1)
        data_splitter = DataSourceSplitter(self.cv_folds, validation_blocks=self.validation_blocks)
        data_producer = data_splitter.build(data)
        objective_evaluate = PipelineObjectiveEvaluate(objective, data_producer,
                                                       time_constraint=self.eval_time_constraint,
                                                       eval_n_jobs=self.n_jobs,  # because tuners are not parallelized
                                                       validation_blocks=data_splitter.validation_blocks)
        tuner = self.tuner_class(objective_evaluate=objective_evaluate,
                                 adapter=self.adapter,
                                 iterations=self.iterations,
                                 early_stopping_rounds=self.early_stopping_rounds,
                                 timeout=self.timeout,
                                 search_space=self.search_space,
                                 n_jobs=self.n_jobs,
                                 **self.additional_params)
        return tuner
