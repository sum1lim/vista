from typing import List, Dict, Any, Optional
import random
import numpy as np
import torch

import vista
from .buffered_dataset import BufferedDataset
from .utils import transform_rgb, pure_pursuit


__all__ = ['VistaDataset', 'worker_init_fn']


class VistaDataset(BufferedDataset):
    def __init__(self, 
                 trace_paths: List[str],
                 trace_config: Dict[str, Any],
                 car_config: Dict[str, Any],
                 reset_config: Dict[str, Any],
                 optimal_control_config: Dict[str, Any],
                 camera_config: Dict[str, Any],
                 train: Optional[bool] = False,
                 buffer_size: Optional[int] = 1,
                 snippet_size: Optional[int] = 100,
                 shuffle: Optional[bool] = False,
                 **kwargs):
        super(VistaDataset, self).__init__(trace_paths, trace_config, car_config,
            train, buffer_size, snippet_size, shuffle)

        assert self.car_config['lookahead_road'] == True, \
            'Require lookahead_raod = True for optimal control'

        self._reset_config = reset_config
        self._optimal_control_config = optimal_control_config
        self._camera_config = camera_config

    def _simulate(self):
        # Initialization for singl-processing dataloader
        worker_info = torch.utils.data.get_worker_info()
        if worker_info is None:
            self._world = vista.World(self.trace_paths, self.trace_config)
            self._agent = self._world.spawn_agent(self.car_config)
            self._camera = self._agent.spawn_camera(self.camera_config)
            self._world.reset()

        # Data generator from simulation
        self._snippet_i = 0
        while True:
            # reset simulator
            if self._agent.done or self._snippet_i >= self.snippet_size:
                if worker_info is not None:
                    self._world.set_seed(worker_info.id)
                self._world.reset({self._agent.id: self.initial_dynamics_fn})
                self._snippet_i = 0

            # optimal control
            curvature, speed = pure_pursuit(self._agent, self.optimal_control_config)

            # step simulator
            action = np.array([curvature, speed])
            self._agent.step_dynamics(action)
            self._agent.step_sensors()
            sensor_name = self._camera.name
            img = self._agent.observations[sensor_name]

            # preprocess and produce data-label pairs
            img = transform_rgb(img, self._camera, self.train)
            label = np.array([curvature]).astype(np.float32)

            self._snippet_i += 1

            yield {'camera': img, 'target': label}

    def initial_dynamics_fn(self, x, y, yaw, steering, speed):
        return [
            x + self._rng.uniform(*self.reset_config['x_perturbation']),
            y,
            yaw + self._rng.uniform(*self.reset_config['yaw_perturbation']),
            steering,
            speed,
        ]

    @property
    def optimal_control_config(self) -> Dict[str, Any]:
        return self._optimal_control_config

    @property
    def reset_config(self) -> Dict[str, Any]:
        return self._reset_config

    @property
    def camera_config(self) -> Dict[str, Any]:
        return self._camera_config


def worker_init_fn(worker_id):
    worker_info = torch.utils.data.get_worker_info()
    dataset = worker_info.dataset
    dataset._world = vista.World(dataset.trace_paths, dataset.trace_config)
    dataset._agent = dataset._world.spawn_agent(dataset.car_config)
    dataset._camera = dataset._agent.spawn_camera(dataset.camera_config)
    dataset._world.set_seed(worker_id)
    dataset._rng = random.Random(worker_id)
    dataset._world.reset({dataset._agent.id: dataset.initial_dynamics_fn})
