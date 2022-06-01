import torch
import torch.optim as optim

from .base_agent import BaseAgent

class BootstrapAgent2(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model = self._model.model
        self._optimizer = [optim.Adam(net.parameters(), lr=self.lr) for net in self._model]

    def chose_action(self, state: torch.Tensor):
        alpha_list = []
        beta_list = []
        v_list = []
        for net in self._model:
            (alpha, beta), v = net(state)
            alpha_list.append(alpha)
            beta_list.append(beta)
            v_list.append(v)
        alpha_list = torch.stack(alpha_list)
        beta_list = torch.stack(beta_list)
        v_list = torch.stack(v_list)
        return (torch.mean(alpha_list, dim=0), torch.mean(beta_list, dim=0)), torch.mean(v_list, dim=0)

    def get_uncert(self, state: torch.Tensor):
        alpha_list = []
        beta_list = []
        v_list = []
        for net in self._model:
            (alpha, beta), v = net(state)
            alpha_list.append(alpha)
            beta_list.append(beta)
            v_list.append(v)

        alpha_list = torch.stack(alpha_list)
        beta_list = torch.stack(beta_list)
        v_list = torch.stack(v_list)

        epistemic = torch.std(v_list)
        aleatoric = torch.tensor([0])
        return (torch.mean(alpha_list, dim=0), torch.mean(beta_list, dim=0)), torch.mean(v_list, dim=0), (epistemic, aleatoric)

    def update(self):
        self.training_step += 1
        s, a, r, s_, old_a_logp = self.unpack_buffer()
        with torch.no_grad():
            target_v = r + self.gamma * self.chose_action(s_)[1].squeeze(dim=-1)
            adv = target_v - self.chose_action(s)[1].squeeze(dim=-1)

        # Random bagging
        # indices = [torch.utils.data.RandomSampler(range(
        #     self.buffer_capacity), num_samples=self.buffer_capacity, replacement=True) for _ in range(self.nb_nets)]
        # Random permutation
        indices = [torch.randperm(self._buffer._capacity) for _ in range(self.nb_nets)]

        for _ in range(self.ppo_epoch):

            for net, optimizer, index in zip(self._model, self._optimizer, indices):
                losses = self.train_once(
                    net,
                    optimizer,
                    target_v, adv, old_a_logp, s, a, index)

            self._logger.log(losses)
            self._nb_update += 1
    
    def get_value_loss(self, prediction, target_v):
        return self._criterion(prediction[1], target_v)

    def save(self, epoch, path="param/ppo_net_params.pkl"):
        tosave = {'epoch': epoch}
        for idx, (net, optimizer) in enumerate(zip(self._model, self._optimizer)):
            tosave['model_state_dict{}'.format(idx)] = net.state_dict()
            tosave['optimizer_state_dict{}'.format(
                idx)] = optimizer.state_dict()
        torch.save(tosave, path)

    def load(self, path, eval_mode=False):
        checkpoint = torch.load(path)
        for idx in range(len(self._model)):
            self._model[idx].load_state_dict(
                checkpoint['model_state_dict{}'.format(idx)])
            self._optimizer[idx].load_state_dict(
                checkpoint['optimizer_state_dict{}'.format(idx)])
            if eval_mode:
                self._model[idx].eval()
            else:
                self._model[idx].train()
        return checkpoint['epoch']