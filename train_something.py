from trexenv import TrexEnv
import os
from typing import Callable
import numpy as np
import pettingzoo as pz
import stable_baselines3 as sb3
import supersuit as ss
from supersuit.vector.sb3_vector_wrapper import SB3VecEnvWrapper
from pettingzoo.test import parallel_api_test
import datetime
from sb3_contrib import RecurrentPPO
# from TREX_env._utils.custom_distributions import SquashedDiagGaussianDistribution as Squash
from TREX_env._utils.ppo_recurrent_custom import RecurrentPPO
from TREX_env._utils.custom_Monitor import Custom_VecMonitor
# from TREX_env._utils.custom_distributions import SquashedDiagGaussianDistribution
from stable_baselines3.common.evaluation import evaluate_policy
from stable_baselines3.common.callbacks import EvalCallback
from stable_baselines3.common.vec_env import VecNormalize, VecMonitor, VecFrameStack, VecCheckNan
from stable_baselines3.common.distributions import SquashedDiagGaussianDistribution
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.her.her_replay_buffer import HerReplayBuffer
from gymnasium.wrappers import FrameStack, NormalizeObservation


def exponential_schedule(initial_value: float, numer_of_steps: int, exponent: float) -> Callable[[float], float]:
    """
    stepwise learning rate schedule.
    We start at initial learning rate,
    and reduce the learning rate by 50% every 1/numer_of_steps of the total number of steps (as indicated by progress remaining)

    :param initial_value: Initial learning rate.
    :return: schedule that computes
      current learning rate depending on remaining progress
    """

    list_of_multipliers = [1]
    for i in range(numer_of_steps-1):
        list_of_multipliers.append(list_of_multipliers[-1] * exponent)
    def func(progress_remaining: float) -> float:
        """
        Progress will decrease from 1 (beginning) to 0 (end).
        We start at initial learning rate,
        and reduce the learning rate by 50% every 1/numer_of_steps of the total number of steps (as indicated by progress remaining)

        :param progress_remaining:
        :return: current learning rate
        """
        mutliplier_index = numer_of_steps - int(np.ceil(progress_remaining * numer_of_steps))
        return list_of_multipliers[mutliplier_index] * initial_value

    return func

if "__main__" == __name__:  # this is needed to make sure the code is not executed when imported
    config_name = "MultiHouseTest_Year_NewMarket"

    current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tboard_logdir = f"runs/{current_time}"

    trex_env = TrexEnv(config_name=config_name,
                       action_space_type='continuous',
                       action_space_entries=None,
                       baseline_offset_rewards=True,
                       one_hot_encode_agent_ids=True,
                       )
    #trex_env = ss.frame_stack_v1(trex_env, 4)

    num_bits = trex_env.num_one_hot_bits
    agents_obs_keys = trex_env.agents_obs_names
    episode_length = trex_env.episode_length
    agents = list(key for key in agents_obs_keys.keys())
    agents_obs_keys = agents_obs_keys[agents[0]]
    print('number of one hot bits', num_bits)

    trex_env = ss.pettingzoo_env_to_vec_env_v1(trex_env)

    # trex_env = ss.flatten_v0(trex_env, )
    # trex_env = ss.normalize_obs_v0(trex_env, env_min=0, env_max=1)

    # trex_env = FrameStack(trex_env, num_stack=5)
    # trex_env = FlattenObservation(trex_env)
    trex_env = SB3VecEnvWrapper(trex_env)
    num_envs = trex_env.num_envs
    print('number of pseudo envs', num_envs)


    # trex_env = VecNormalize(trex_env, norm_obs=True, norm_reward=False, clip_obs=np.inf, clip_reward=np.inf, gamma=0.99,
    #             epsilon=1e-08)
    # trex_env = VecFrameStack(trex_env, n_stack=5)
    unnormalized_env = Custom_VecMonitor(trex_env, filename=tboard_logdir, obs_names=agents_obs_keys) #can add extra arguments to monitor in info keywords, look up https://stable-baselines3.readthedocs.io/en/master/_modules/stable_baselines3/common/vec_env/vec_monitor.html
    final_env = VecNormalize(unnormalized_env, norm_obs=True, norm_reward=False,
                                        num_bits=num_bits,
                                         clip_obs=1e6, clip_reward=1e6, gamma=0.99, epsilon=1e-08)
    # final_env = VecCheckNan(final_env)
    # final_env = VecFrameStack(final_env, n_stack=12, channels_order='first')
    # trex_env = ss.concat_vec_envs_v1(trex_env, 1, base_class="stable_baselines3")


    # trex_env = ss.vector.markov_vector_wrapper.MarkovVectorEnv(trex_env)
    # get current time to add to tensoboard logdic

    #set up Recurrent PPO
    obs_space = final_env.observation_space
    action_space = final_env.action_space
    num_actions = final_env.action_space.shape[0]
    policy_kwargs = dict(shared_lstm=False,
                         share_features_extractor=True,
                        lstm_hidden_size=256,
                         net_arch=dict(pi=[64], vf=[64]),
                         n_lstm_layers=2,
                         # log_std=-10
                         )
    # if we have a models folder, then we load the most recent model
    save_path = 'PPO_Models_schedule_2'
    # if os.path.exists(save_path):
    #     # list all saved model zip files in the model folder
    #     model_list = [model for model in os.listdir(save_path) if model.endswith('.zip')]
    #     # sort the list by date, most recent first
    #     model_list.sort(key=lambda x: os.path.getmtime(os.path.join(save_path, x)), reverse=True)
    #     # load the most recent model
    #     model_to_load = model_list[0]
    #     # remove the zip ending
    #     model_to_load = model_to_load[:-4]
    #     model_path = os.path.join(save_path, model_to_load)
    #     print('loading model', model_path)
    #     model = RecurrentPPO.load(model_path, env=final_env, tensorboard_log=tboard_logdir, device="cuda", verbose=0)
   #  else:
    model = RecurrentPPO('MlpLstmPolicy',
                     final_env,
                     verbose=0,
                     use_sde=False,
                     tensorboard_log=tboard_logdir,
                     device="cuda",
                     n_epochs=4,
                     # target_kl=0.05,
                         learning_rate=exponential_schedule(0.0003, 15, 0.69),
                     n_steps=9*24,
                     stats_window_size=1,
                     ent_coef=0.00,
                     # policy_kwargs=policy_dict,
                     batch_size=3*24,
                     recalculate_lstm_states=True,
                     rewards_shift=2,
                     self_bootstrap_dones=True,
                     )

    model.policy.action_dist = SquashedDiagGaussianDistribution(action_dim=num_actions, epsilon=1e-5)
    # eval_callback = EvalCallback(eval_env=final_env,
    #                              best_model_save_path="models/",
    #                              log_path=tboard_logdir, n_eval_episodes=1,
    #                              eval_freq= 10*24*100, deterministic=True, render=False)

    checkpoint_callback = CheckpointCallback(save_freq=5 * episode_length,
                                             save_path=save_path,
                                             name_prefix='ALEX_PPO_256',
                                             save_replay_buffer=True,
                                             save_vecnormalize=True,
                                             verbose=2,
                                             )

    model.learn(total_timesteps=20*1e6,
                callback=checkpoint_callback,
                )
    #evaluate the model, add the reward values to the tensorboard log
    #reset the env first
    # obs = trex_env.reset()
    #evaluate the model
    # mean_reward, std_reward = evaluate_policy(model, trex_env, n_eval_episodes=trex_env.num_envs, deterministic=True)
    #add the reward values to the tensorboard log
    # model.logger.record("eval/mean_reward", mean_reward)
    # model.logger.record("eval/std_reward", std_reward)