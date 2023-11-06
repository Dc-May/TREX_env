import time
import warnings
from typing import Optional, Tuple

import numpy as np
import os
import torch as th


from stable_baselines3.common.vec_env.base_vec_env import VecEnv, VecEnvObs, VecEnvStepReturn, VecEnvWrapper

from stable_baselines3.common.logger import TensorBoardOutputFormat
class Custom_VecMonitor(VecEnvWrapper):
    """
    A vectorized monitor wrapper for *vectorized* Gym environments,
    it is used to record the episode reward, length, time and other data.

    Some environments like `openai/procgen <https://github.com/openai/procgen>`_
    or `gym3 <https://github.com/openai/gym3>`_ directly initialize the
    vectorized environments, without giving us a chance to use the ``Monitor``
    wrapper. So this class simply does the job of the ``Monitor`` wrapper on
    a vectorized level.

    :param venv: The vectorized environment
    :param filename: the location to save a log file, can be None for no log
    :param info_keywords: extra information to log, from the information return of env.step()
    """

    def __init__(
        self,
        venv: VecEnv,
        filename: Optional[str] = None,
        info_keywords: Tuple[str, ...] = (),
    ):
        # Avoid circular import
        from stable_baselines3.common.monitor import Monitor, ResultsWriter

        # This check is not valid for special `VecEnv`
        # like the ones created by Procgen, that does follow completely
        # the `VecEnv` interface
        try:
            is_wrapped_with_monitor = venv.env_is_wrapped(Monitor)[0]
        except AttributeError:
            is_wrapped_with_monitor = False

        if is_wrapped_with_monitor:
            warnings.warn(
                "The environment is already wrapped with a `Monitor` wrapper"
                "but you are wrapping it with a `VecMonitor` wrapper, the `Monitor` statistics will be"
                "overwritten by the `VecMonitor` ones.",
                UserWarning,
            )

        VecEnvWrapper.__init__(self, venv)
        self.episode_count = 0
        self.t_start = time.time()

        env_id = None
        if hasattr(venv, "spec") and venv.spec is not None:
            env_id = venv.spec.id

        self.results_writer: Optional[ResultsWriter] = None
        if filename:
            file = os.path.join(filename, 'custom_metrics')
            self.results_writer = TensorBoardOutputFormat(folder=file)

        self.info_keywords = info_keywords
        self.episode_returns = np.zeros(self.num_envs, dtype=np.float32)
        self.episode_lengths = np.zeros(self.num_envs, dtype=np.int32)

    def reset(self) -> VecEnvObs:
            obs = self.venv.reset()
            self.episode_returns = np.zeros(self.num_envs, dtype=np.float32)
            self.episode_lengths = np.zeros(self.num_envs, dtype=np.int32)
            return obs

    def step_wait(self) -> VecEnvStepReturn:
            obs, rewards, dones, infos = self.venv.step_wait()
            self.episode_returns += rewards
            self.episode_lengths += 1
            new_infos = list(infos[:])
            if all(dones): #This assumes termination of all envs at the same time!!
                assert len(dones) == self.num_envs

                self.results_writer.writer.add_histogram('custom_metrics/Building_returns', th.Tensor(self.episode_returns), self.episode_count)
                self.results_writer.writer.add_scalar('custom_metrics/Building_mean_returns', np.mean(self.episode_returns), self.episode_count)

                for environment in range(self.num_envs):
                    self.results_writer.writer.add_scalar('custom_metrics/Building_' + str(environment + 1),self.episode_returns[environment], self.episode_count)


                for i in range(len(dones)):
                    info = infos[i].copy()
                    episode_return = self.episode_returns[i]
                    episode_length = self.episode_lengths[i]
                    episode_info = {"r": episode_return,
                                    "l": episode_length,
                                    "t": round(time.time() - self.t_start, 6)
                                    }
                    for key in self.info_keywords:
                        episode_info[key] = info[key]
                    info["episode"] = episode_info
                    self.episode_count += 1
                    self.episode_returns[i] = 0
                    self.episode_lengths[i] = 0
                    new_infos[i] = info
            return obs, rewards, dones, new_infos

    def close(self) -> None:
            if self.results_writer:
                self.results_writer.close()
            return self.venv.close()