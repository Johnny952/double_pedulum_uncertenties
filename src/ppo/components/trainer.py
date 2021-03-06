import numpy as np
from tqdm import tqdm

from shared.utils.utils import save_uncert
from shared.components.evaluator import Evaluator
from components.uncert_agents.base_agent import BaseAgent
from shared.components.env import Env
from shared.components.logger import Logger
from shared.utils.adjust_range import adjust_range

class Trainer:
    def __init__(
        self,
        agent: BaseAgent,
        env: Env,
        eval_env: Env,
        logger: Logger,
        episodes: int,
        init_ep: int = 0,
        nb_evaluations: int = 1,
        eval_interval: int = 10,
        model_name="base",
        checkpoint_every=10,
        debug=False,
        evaluator: Evaluator = None,
    ) -> None:
        self._logger = logger
        self._agent = agent
        self._env = env
        self._eval_env = eval_env
        self._init_ep = init_ep
        self._nb_episodes = episodes
        self._nb_evaluations = nb_evaluations
        self._eval_interval = eval_interval
        self._model_name = model_name
        self._checkpoint_every = checkpoint_every
        self._debug = debug
        self._evaluator = evaluator

        self.best_model_path = f"param/best_{model_name}.pkl"
        self.checkpoint_model_path = f"param/checkpoint_{self._model_name}.pkl"

        self._best_score = -100
        self._eval_nb = 0
        self._max_running_score = 0

    def run(self):
        running_score = 0

        for i_ep in tqdm(range(self._init_ep, self._nb_episodes), 'Training'):
            metrics = {
                "Train Episode": i_ep,
                "Episode Running Score": float(running_score),
                "Episode Score": 0,
                "Episode Steps": 0,
            }
            state = self._env.reset()

            for _ in range(1000):
                action, a_logp = self._agent.select_action(state)[:2]
                state_, reward, done, die = self._env.step(adjust_range(action, target_range=self._env.observation_space))[:4]
                if self._agent.store_transition(state, action, reward, state_, a_logp):
                    self._agent.update()
                    self._agent.empty_buffer()
                metrics["Episode Score"] += reward
                metrics["Episode Steps"] += 1
                state = state_

                if done or die:
                    break
            running_score = running_score * 0.99 + metrics["Episode Score"] * 0.01
            if running_score > self._max_running_score:
                self._max_running_score = running_score
            metrics["Episode Running Score"] = running_score
            metrics["Max Episode Running Score"] = self._max_running_score
            self._logger.log(metrics)

            # Eval agent
            if (i_ep + 1) % self._eval_interval == 0:
                eval_score = self.eval(i_ep)

                if eval_score > self._best_score and not self._debug:
                    self._agent.save(i_ep, path=self.best_model_path)
                    self._best_score = eval_score
            # Save checkpoint
            if (i_ep + 1) % self._checkpoint_every == 0 and not self._debug:
                self._agent.save(i_ep, path=self.checkpoint_model_path)
            # Stop training
            if running_score > self._env.reward_threshold:
                print(
                    "Solved! Running reward is now {} and the last episode runs to {}!".format(
                        running_score, metrics["Episode Score"]
                    )
                )
                if not self._debug:
                    self._agent.save(i_ep, path=self.best_model_path)
                break

    def eval(self, episode_nb, mode='eval'):
        assert mode in ['train', 'eval', 'test0', 'test']
        if self._evaluator:
            self._evaluator.eval(episode_nb, self._agent)
        # self._agent.eval_mode()
        wandb_mode = mode.title()
        metrics = {
            f"{wandb_mode} Episode": self._eval_nb,
            f"{wandb_mode} Mean Score": 0,
            f"{wandb_mode} Mean Epist Uncert": 0,
            f"{wandb_mode} Mean Aleat Uncert": 0,
            f"{wandb_mode} Mean Steps": 0,
        }
        mean_uncert = np.array([0, 0], dtype=np.float64)

        for i_val in tqdm(range(self._nb_evaluations), f'{wandb_mode} ep {episode_nb}'):

            score = 0
            steps = 0
            state = self._eval_env.reset()
            die = False

            uncert = []
            while not die:
                action, _, (epis, aleat) = self._agent.select_action(state, eval=True)
                uncert.append(
                    [epis.view(-1).cpu().numpy()[0], aleat.view(-1).cpu().numpy()[0]]
                )
                state_, reward, _, die = self._eval_env.step(adjust_range(action, target_range=self._env.observation_space))[:4]
                score += reward
                state = state_
                steps += 1

            uncert = np.array(uncert)
            if not self._debug:
                save_uncert(
                    episode_nb,
                    i_val,
                    score,
                    uncert,
                    file=f"uncertainties/{mode}/{self._model_name}.txt",
                    sigma=self._eval_env.random_noise,
                )

            mean_uncert += np.mean(uncert, axis=0) / self._nb_evaluations
            metrics[f"{wandb_mode} Mean Score"] += score / self._nb_evaluations
            metrics[f"{wandb_mode} Mean Steps"] += steps / self._nb_evaluations
        metrics[f"{wandb_mode} Mean Epist Uncert"] = mean_uncert[0]
        metrics[f"{wandb_mode} Mean Aleat Uncert"] = mean_uncert[1]

        self._logger.log(metrics)
        self._eval_nb += 1

        return metrics[f"{wandb_mode} Mean Score"]