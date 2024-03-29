"""Probability distributions."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, TypeVar, Union

import numpy as np
import torch as th
from gymnasium import spaces
from torch import nn
from torch.distributions import Bernoulli, Categorical, Normal

from stable_baselines3.common.preprocessing import get_action_dim
from stable_baselines3.common.distributions import Distribution, TanhBijector, sum_independent_dims

SelfDiagGaussianDistribution = TypeVar("SelfDiagGaussianDistribution", bound="DiagGaussianDistribution")
SelfSquashedDiagGaussianDistribution = TypeVar(
    "SelfSquashedDiagGaussianDistribution", bound="SquashedDiagGaussianDistribution"
)

def random_uniform(shape, lower, upper):
    return (lower - upper) * th.rand(shape) + upper

class DiagGaussianDistribution(Distribution):
    """
    Gaussian distribution with diagonal covariance matrix, for continuous actions.

    :param action_dim:  Dimension of the action space.
    """

    def __init__(self, action_dim: int, std_bias: float = -3.0):
        super().__init__()
        self.action_dim = action_dim
        self.mean_actions = None
        self.std_bias = std_bias
    def proba_distribution_net(self, latent_dim: int, log_std_init: float = 0.0) -> Tuple[nn.Module, nn.Parameter]:
        """
        Create the layers and parameter that represent the distribution:
        one output will be the mean of the Gaussian, the other parameter will be the
        standard deviation (log std in fact to allow negative values)

        :param latent_dim: Dimension of the last layer of the policy (before the action layer)
        :param log_std_init: Initial value for the log standard deviation
        :return:
        """

        mean_actions = nn.Linear(latent_dim, self.action_dim, bias=True)
        mean_init_weights = random_uniform(mean_actions.weight.shape, -1e3, 1e3)
        mean_actions.weight = th.nn.Parameter(mean_init_weights, requires_grad=True)

        mean_init_bias = random_uniform(mean_actions.weight.shape, -1e3, 1e3)
        mean_actions.bias = th.nn.Parameter(mean_init_bias, requires_grad=True)

        # TODO: allow action dependent std
        log_std = nn.Linear(latent_dim, self.action_dim, bias=True)
        mean_init_weights = random_uniform(log_std.weight.shape, -1e3, 1e3)
        log_std.weight = th.nn.Parameter(mean_init_weights, requires_grad=True)

        mean_init_bias = random_uniform(log_std.bias.shape, -1e3 + self.std_bias, 1e-3 + self.std_bias)
        log_std.bias = th.nn.Parameter(mean_init_bias, requires_grad=True)

        return mean_actions, log_std

    def proba_distribution(
        self: SelfDiagGaussianDistribution, mean_actions: th.Tensor, log_std: th.Tensor
    ) -> SelfDiagGaussianDistribution:
        """
        Create the distribution given its parameters (mean, std)

        :param mean_actions:
        :param log_std:
        :return:
        """
        action_std = th.ones_like(mean_actions) * log_std.exp()
        self.distribution = Normal(mean_actions, action_std)
        return self
    def log_prob(self, actions: th.Tensor) -> th.Tensor:
        """
        Get the log probabilities of actions according to the distribution.
        Note that you must first call the ``proba_distribution()`` method.

        :param actions:
        :return:
        """
        log_prob = self.distribution.log_prob(actions)
        return sum_independent_dims(log_prob)
    def entropy(self) -> th.Tensor:
        return sum_independent_dims(self.distribution.entropy())
    def sample(self) -> th.Tensor:
        # Reparametrization trick to pass gradients
        return self.distribution.rsample()
    def mode(self) -> th.Tensor:
        return self.distribution.mean
    def actions_from_params(self, mean_actions: th.Tensor, log_std: th.Tensor, deterministic: bool = False) -> th.Tensor:
        # Update the proba distribution
        self.proba_distribution(mean_actions, log_std)
        return self.get_actions(deterministic=deterministic)
    def log_prob_from_params(self, mean_actions: th.Tensor, log_std: th.Tensor) -> Tuple[th.Tensor, th.Tensor]:
        """
        Compute the log probability of taking an action
        given the distribution parameters.

        :param mean_actions:
        :param log_std:
        :return:
        """
        actions = self.actions_from_params(mean_actions, log_std)
        log_prob = self.log_prob(actions)
        return actions, log_prob

class SquashedDiagGaussianDistribution(DiagGaussianDistribution):
    """
    Gaussian distribution with diagonal covariance matrix, followed by a squashing function (tanh) to ensure bounds.

    :param action_dim: Dimension of the action space.
    :param epsilon: small value to avoid NaN due to numerical imprecision.
    """

    def __init__(self, action_dim: int, epsilon: float = 1e-6, std_bias: float = -3.0,):
        super().__init__(action_dim, std_bias)
        # Avoid NaN (prevents division by zero or log of zero)
        self.epsilon = epsilon
        self.gaussian_actions = None

    def proba_distribution(
        self: SelfSquashedDiagGaussianDistribution, mean_actions: th.Tensor, log_std: th.Tensor
    ) -> SelfSquashedDiagGaussianDistribution:
        super().proba_distribution(mean_actions, log_std)
        return self

    def log_prob(self, actions: th.Tensor, gaussian_actions: Optional[th.Tensor] = None) -> th.Tensor:
        # Inverse tanh
        # Naive implementation (not stable): 0.5 * torch.log((1 + x) / (1 - x))
        # We use numpy to avoid numerical instability
        if gaussian_actions is None:
            # It will be clipped to avoid NaN when inversing tanh
            gaussian_actions = TanhBijector.inverse(actions)

        # Log likelihood for a Gaussian distribution
        log_prob = super().log_prob(gaussian_actions)
        # Squash correction (from original SAC implementation)
        # this comes from the fact that tanh is bijective and differentiable
        log_prob -= th.sum(th.log(1 - actions**2 + self.epsilon), dim=1)
        return log_prob

    def entropy(self) -> Optional[th.Tensor]:
        # No analytical form,
        # entropy needs to be estimated using -log_prob.mean()
        return sum_independent_dims(self.distribution.entropy())

    def sample(self) -> th.Tensor:
        # Reparametrization trick to pass gradients
        self.gaussian_actions = super().sample()
        return th.tanh(self.gaussian_actions)

    def mode(self) -> th.Tensor:
        self.gaussian_actions = super().mode()
        # Squash the output
        return th.tanh(self.gaussian_actions)

    def log_prob_from_params(self, mean_actions: th.Tensor, log_std: th.Tensor) -> Tuple[th.Tensor, th.Tensor]:
        action = self.actions_from_params(mean_actions, log_std)
        log_prob = self.log_prob(action, self.gaussian_actions)
        return action, log_prob