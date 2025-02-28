import os.path
import sys
from abc import abstractmethod
from pathlib import Path
from uuid import uuid4

import numpy as np
from sklearn.metrics import (accuracy_score, auc, f1_score, log_loss, mean_absolute_error,
                             mean_absolute_percentage_error, mean_squared_error, mean_squared_log_error,
                             precision_score, r2_score, roc_auc_score, roc_curve, silhouette_score)
from sktime.performance_metrics.forecasting import mean_absolute_scaled_error

from fedot.core.data.data import InputData, OutputData
from fedot.core.pipelines.pipeline import Pipeline
from fedot.core.pipelines.ts_wrappers import in_sample_ts_forecast
from fedot.core.repository.dataset_types import DataTypesEnum
from fedot.core.repository.tasks import TaskTypesEnum
from fedot.core.utils import default_fedot_data_dir
from fedot.utilities.custom_errors import AbstractMethodNotImplementError
from fedot.utilities.debug import is_analytic_mode


def from_maximised_metric(metric_func):
    def wrapper(*args, **kwargs):
        return -metric_func(*args, **kwargs)

    return wrapper


def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """ Symmetric mean absolute percentage error """

    numerator = 2 * np.abs(y_true - y_pred)
    denominator = np.abs(y_true) + np.abs(y_pred)
    result = numerator / denominator
    result[np.isnan(result)] = 0.0
    return float(np.mean(100 * result))


class Metric:
    output_mode = 'default'
    default_value = 0

    @classmethod
    @abstractmethod
    def get_value(cls, pipeline: 'Pipeline', reference_data: InputData,
                  validation_blocks: int) -> float:
        """ Get metrics values based on pipeline and InputData for validation """
        raise AbstractMethodNotImplementError

    @staticmethod
    @abstractmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        raise AbstractMethodNotImplementError


class QualityMetric:
    max_penalty_part = 0.01
    output_mode = 'default'
    default_value = 0

    @staticmethod
    @abstractmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        raise AbstractMethodNotImplementError

    @classmethod
    def get_value(cls, pipeline: 'Pipeline', reference_data: InputData,
                  validation_blocks: int = None) -> float:
        metric = cls.default_value
        try:
            if validation_blocks is None:
                # Time series or regression classical hold-out validation
                results, reference_data = cls._simple_prediction(pipeline, reference_data)
            else:
                # Perform time series in-sample validation
                reference_data, results = cls._in_sample_prediction(pipeline, reference_data, validation_blocks)
            metric = cls.metric(reference_data, results)

            if is_analytic_mode():
                from fedot.core.data.visualisation import plot_forecast

                pipeline_id = str(uuid4())
                save_path = Path(default_fedot_data_dir(), 'ts_forecasting_debug', pipeline_id)
                if not os.path.exists(save_path):
                    os.makedirs(save_path)
                pipeline.show(save_path=Path(save_path, 'pipeline.png'))
                plot_forecast(reference_data, results, in_sample=True,
                              title=f'Forecast with metric {round(metric, 4)}',
                              save_path=Path(save_path, 'forecast.png'))

        except Exception as ex:
            pipeline.log.info(f'Metric can not be evaluated because of: {ex}', raise_if_test=True)

        return metric

    @classmethod
    def _simple_prediction(cls, pipeline: 'Pipeline', reference_data: InputData):
        """ Method prepares data for metric evaluation and perform simple validation """
        results = pipeline.predict(reference_data, output_mode=cls.output_mode)

        # Define conditions for target and predictions transforming
        is_regression = reference_data.task.task_type == TaskTypesEnum.regression
        is_multi_target = len(np.array(results.predict).shape) > 1
        is_multi_target_regression = is_regression and is_multi_target

        # Time series forecasting
        is_ts_forecasting = reference_data.task.task_type == TaskTypesEnum.ts_forecasting
        if is_ts_forecasting or is_multi_target_regression:
            results, reference_data = cls.flatten_convert(results, reference_data)

        return results, reference_data

    @staticmethod
    def flatten_convert(results, reference_data):
        """ Transform target and predictions by converting them into
        one-dimensional array

        :param results: output from pipeline
        :param reference_data: actual data for validation
        """
        # Predictions convert into uni-variate array
        forecast_values = np.ravel(np.array(results.predict))
        results.predict = forecast_values
        # Target convert into uni-variate array
        target_values = np.ravel(np.array(reference_data.target))
        reference_data.target = target_values

        return results, reference_data

    @classmethod
    def get_value_with_penalty(cls, pipeline: 'Pipeline', reference_data: InputData,
                               validation_blocks: int = None) -> float:
        quality_metric = cls.get_value(pipeline, reference_data)
        structural_metric = StructuralComplexity.get_value(pipeline)

        penalty = abs(structural_metric * quality_metric * cls.max_penalty_part)
        metric_with_penalty = (quality_metric +
                               min(penalty, abs(quality_metric * cls.max_penalty_part)))
        return metric_with_penalty

    @staticmethod
    def _in_sample_prediction(pipeline: 'Pipeline', data: InputData, validation_blocks: int):
        """ Performs in-sample pipeline validation for time series prediction """

        horizon = int(validation_blocks * data.task.task_params.forecast_length)

        actual_values = data.target[-horizon:]

        predicted_values = in_sample_ts_forecast(pipeline=pipeline,
                                                 input_data=data,
                                                 horizon=horizon)

        # Wrap target and prediction arrays into OutputData and InputData
        results = OutputData(idx=np.arange(0, len(predicted_values)), features=predicted_values,
                             predict=predicted_values, task=data.task, target=predicted_values,
                             data_type=DataTypesEnum.ts)
        reference_data = InputData(idx=np.arange(0, len(actual_values)), features=actual_values,
                                   task=data.task, target=actual_values, data_type=DataTypesEnum.ts)

        return reference_data, results


class RMSE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return mean_squared_error(y_true=reference.target,
                                  y_pred=predicted.predict, squared=False)


class MSE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return mean_squared_error(y_true=reference.target,
                                  y_pred=predicted.predict, squared=True)


class MSLE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return mean_squared_log_error(y_true=reference.target,
                                      y_pred=predicted.predict)


class MAPE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return mean_absolute_percentage_error(y_true=reference.target,
                                              y_pred=predicted.predict)


class SMAPE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return smape(y_true=reference.target, y_pred=predicted.predict)


class F1(QualityMetric):
    default_value = 0
    output_mode = 'labels'
    binary_averaging_mode = 'binary'
    multiclass_averaging_mode = 'weighted'

    @staticmethod
    @from_maximised_metric
    def metric(reference: InputData, predicted: OutputData) -> float:
        n_classes = reference.num_classes
        if n_classes > 2:
            additional_params = {'average': F1.multiclass_averaging_mode}
        else:
            u, count = np.unique(np.ravel(reference.target), return_counts=True)
            count_sort_ind = np.argsort(count)
            pos_label = u[count_sort_ind[0]].item()
            additional_params = {'average': F1.binary_averaging_mode, 'pos_label': pos_label}
        return f1_score(y_true=reference.target, y_pred=predicted.predict,
                        **additional_params)


class MAE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return mean_absolute_error(y_true=reference.target, y_pred=predicted.predict)


class MASE(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        history_series = reference.features
        return mean_absolute_scaled_error(y_true=reference.target, y_pred=predicted.predict, y_train=history_series)


class R2(QualityMetric):
    default_value = 0

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return r2_score(y_true=reference.target, y_pred=predicted.predict)


class ROCAUC(QualityMetric):
    default_value = 0.5

    @staticmethod
    @from_maximised_metric
    def metric(reference: InputData, predicted: OutputData) -> float:
        n_classes = reference.num_classes
        if n_classes > 2:
            additional_params = {'multi_class': 'ovr', 'average': 'macro'}
        else:
            additional_params = {}

        score = round(roc_auc_score(y_score=predicted.predict,
                                    y_true=reference.target,
                                    **additional_params), 3)

        return score

    @staticmethod
    def roc_curve(target: np.ndarray, predict: np.ndarray, pos_label=None):

        return roc_curve(target, predict, pos_label=pos_label)

    @classmethod
    def auc(cls, fpr, tpr):
        return auc(fpr, tpr)


class Precision(QualityMetric):
    output_mode = 'labels'

    @staticmethod
    @from_maximised_metric
    def metric(reference: InputData, predicted: OutputData) -> float:
        n_classes = reference.num_classes
        if n_classes > 2:
            return precision_score(y_true=reference.target, y_pred=predicted.predict)
        else:
            u, count = np.unique(np.ravel(reference.target), return_counts=True)
            count_sort_ind = np.argsort(count)
            pos_label = u[count_sort_ind[0]].item()
            additional_params = {'pos_label': pos_label}
            return precision_score(y_true=reference.target, y_pred=predicted.predict,
                                   **additional_params)


class Logloss(QualityMetric):
    default_value = sys.maxsize

    @staticmethod
    def metric(reference: InputData, predicted: OutputData) -> float:
        return log_loss(y_true=reference.target, y_pred=predicted.predict)


class Accuracy(QualityMetric):
    default_value = 0
    output_mode = 'labels'

    @staticmethod
    @from_maximised_metric
    def metric(reference: InputData, predicted: OutputData) -> float:
        return accuracy_score(y_true=reference.target, y_pred=predicted.predict)


class Silhouette(QualityMetric):
    default_value = 1

    @staticmethod
    @from_maximised_metric
    def metric(reference: InputData, predicted: OutputData) -> float:
        return silhouette_score(reference.features, labels=predicted.predict)


class StructuralComplexity(Metric):
    @classmethod
    def get_value(cls, pipeline: 'Pipeline', **args) -> float:
        norm_constant = 30
        return (pipeline.depth ** 2 + pipeline.length) / norm_constant


class NodeNum(Metric):
    @classmethod
    def get_value(cls, pipeline: 'Pipeline', **args) -> float:
        norm_constant = 10
        return pipeline.length / norm_constant


class ComputationTime(Metric):
    @classmethod
    def get_value(cls, pipeline: 'Pipeline', **args) -> float:
        return pipeline.computation_time
