"""
FileName: explainers.py
Description: Explainable methods' set
Time: 2020/8/4 8:56
Project: GNN_benchmark
Author: Shurui Gui
"""
import copy
from math import sqrt
from typing import Any, Callable, Dict, List, Sequence, Tuple, Union

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from gnn.model import GraphConv, GraphConvolution
from matplotlib.axes import Axes
from matplotlib.patches import Path, PathPatch
from torch import Tensor
from torch.nn import Module
from torch.nn.functional import binary_cross_entropy as bceloss
from torch_geometric.data import Batch, Data
from torch_geometric.nn import MessagePassing
from torch_geometric.utils import to_networkx
from torch_geometric.utils.loop import add_self_loops
from tqdm import tqdm
from utils.gen_utils import from_edge_index_to_adj, subgraph

EPS = 1e-15


class GraphSequential(nn.Sequential):

    def __init__(self, *args):
        super().__init__(*args)

    def forward(self, *input) -> Tensor:
        for module in self:
            if isinstance(input, tuple):
                input = module(*input)
            else:
                input = module(input)
        return 

class GNNPool(nn.Module):
    def __init__(self):
        super().__init__()

class ExplainerBase(nn.Module):

    def __init__(self, model: nn.Module, epochs=0, lr=0, explain_graph=False, molecule=False):
        super().__init__()
        self.model = model
        self.lr = lr
        self.epochs = epochs
        self.explain_graph = explain_graph
        self.molecule = molecule
        self.mp_layers = [module for module in self.model.modules() if isinstance(module, MessagePassing)]
        self.num_layers = len(self.mp_layers)

        self.ori_pred = None
        self.ex_labels = None
        self.edge_mask = None
        self.hard_edge_mask = None

        self.num_edges = None
        self.num_nodes = None
        self.device = None

    def __set_masks__(self, x, edge_index, init="normal"):
        (N, F), E = x.size(), edge_index.size(1)

        std = 0.1
        self.node_feat_mask = torch.nn.Parameter(torch.randn(F, requires_grad=True, device=self.device) * 0.1)

        std = torch.nn.init.calculate_gain('relu') * sqrt(2.0 / (2 * N))
        self.edge_mask = torch.nn.Parameter(torch.randn(E, requires_grad=True, device=self.device) * std)
        # self.edge_mask = torch.nn.Parameter(100 * torch.ones(E, requires_grad=True))

        for module in self.model.modules():
            if isinstance(module, MessagePassing):
                module.__explain__ = True
                module.__edge_mask__ = self.edge_mask

    def __clear_masks__(self):
        for module in self.model.modules():
            if isinstance(module, MessagePassing):
                module.__explain__ = False
                module.__edge_mask__ = None
        self.node_feat_masks = None
        self.edge_mask = None

    @property
    def __num_hops__(self):
        if self.explain_graph:
            return -1
        else:
            return self.num_layers

    def __flow__(self):
        for module in self.model.modules():
            if isinstance(module, MessagePassing):
                return module.flow
        return 'source_to_target'

    def __subgraph__(self, node_idx, x, edge_index, **kwargs):
        num_nodes, num_edges = x.size(0), edge_index.size(1)

        subset, edge_index, mapping, edge_mask = subgraph(
            node_idx, self.__num_hops__, edge_index, relabel_nodes=True,
            num_nodes=num_nodes, flow=self.__flow__())

        x = x[subset]
        for key, item in kwargs.items():
            if torch.is_tensor(item) and item.size(0) == num_nodes:
                item = item[subset]
            elif torch.is_tensor(item) and item.size(0) == num_edges:
                item = item[edge_mask]
            kwargs[key] = item

        return x, edge_index, mapping, edge_mask, kwargs


    def forward(self,
                x: Tensor,
                edge_index: Tensor,
                **kwargs
                ):
        self.num_edges = edge_index.shape[1]
        self.num_nodes = x.shape[0]
        self.device = x.device




    def visualize_graph(self, node_idx, edge_index, edge_mask, y=None,
                           threshold=None, **kwargs) -> Tuple[Axes, nx.DiGraph]:
        r"""Visualizes the subgraph around :attr:`node_idx` given an edge mask
        :attr:`edge_mask`.

        Args:
            node_idx (int): The node id to explain.
            edge_index (LongTensor): The edge indices.
            edge_mask (Tensor): The edge mask.
            y (Tensor, optional): The ground-truth node-prediction labels used
                as node colorings. (default: :obj:`None`)
            threshold (float, optional): Sets a threshold for visualizing
                important edges. If set to :obj:`None`, will visualize all
                edges with transparancy indicating the importance of edges.
                (default: :obj:`None`)
            **kwargs (optional): Additional arguments passed to
                :func:`nx.draw`.

        :rtype: :class:`matplotlib.axes.Axes`, :class:`networkx.DiGraph`
        """
        edge_index, _ = add_self_loops(edge_index, num_nodes=kwargs.get('num_nodes'))
        assert edge_mask.size(0) == edge_index.size(1)

        if self.molecule:
            atomic_num = torch.clone(y)

        # Only operate on a k-hop subgraph around `node_idx`.
        subset, edge_index, _, hard_edge_mask = subgraph(
            node_idx, self.__num_hops__, edge_index, relabel_nodes=True,
            num_nodes=None, flow=self.__flow__())

        edge_mask = edge_mask[hard_edge_mask]

        # --- temp ---
        edge_mask[edge_mask == float('inf')] = 1
        edge_mask[edge_mask == - float('inf')] = 0
        # ---

        if threshold is not None:
            edge_mask = (edge_mask >= threshold).to(torch.float)

        if y is None:
            y = torch.zeros(edge_index.max().item() + 1,
                            device=edge_index.device)
        else:
            y = y[subset]

        if self.molecule:
            atom_colors = {6: '#8c69c5', 7: '#71bcf0', 8: '#aef5f1', 9: '#bdc499', 15: '#c22f72', 16: '#f3ea19',
                           17: '#bdc499', 35: '#cc7161'}
            node_colors = [None for _ in range(y.shape[0])]
            for y_idx in range(y.shape[0]):
                node_colors[y_idx] = atom_colors[y[y_idx].int().tolist()]
        else:
            atom_colors = {0: '#8c69c5', 1: '#c56973', 2: '#a1c569', 3: '#69c5ba'}
            node_colors = [None for _ in range(y.shape[0])]
            for y_idx in range(y.shape[0]):
                node_colors[y_idx] = atom_colors[y[y_idx].int().tolist()]


        data = Data(edge_index=edge_index, att=edge_mask, y=y,
                    num_nodes=y.size(0)).to('cpu')
        G = to_networkx(data, node_attrs=['y'], edge_attrs=['att'])
        mapping = {k: i for k, i in enumerate(subset.tolist())}
        G = nx.relabel_nodes(G, mapping)

        kwargs['with_labels'] = kwargs.get('with_labels') or True
        kwargs['font_size'] = kwargs.get('font_size') or 10
        kwargs['node_size'] = kwargs.get('node_size') or 250
        kwargs['cmap'] = kwargs.get('cmap') or 'cool'

        # calculate Graph positions
        pos = nx.kamada_kawai_layout(G)
        ax = plt.gca()

        for source, target, data in G.edges(data=True):
            ax.annotate(
                '', xy=pos[target], xycoords='data', xytext=pos[source],
                textcoords='data', arrowprops=dict(
                    arrowstyle="->",
                    lw=max(data['att'], 0.5) * 2,
                    alpha=max(data['att'], 0.4),  # alpha control transparency
                    color='#e1442a',  # color control color
                    shrinkA=sqrt(kwargs['node_size']) / 2.0,
                    shrinkB=sqrt(kwargs['node_size']) / 2.0,
                    connectionstyle="arc3,rad=0.08",  # rad control angle
                ))
        nx.draw_networkx_nodes(G, pos, node_color=node_colors, **kwargs)
        # define node labels
        if self.molecule:
            if kwargs.get('nolabel'):
                node_labels = {n: f'{self.table(atomic_num[n].int().item())}'
                               for n in G.nodes()}
                nx.draw_networkx_labels(G, pos, labels=node_labels, **kwargs)
            else:
                node_labels = {n: f'{n}:{self.table(atomic_num[n].int().item())}'
                               for n in G.nodes()}
                nx.draw_networkx_labels(G, pos, labels=node_labels, **kwargs)
        else:
            if not kwargs.get('nolabel'):
                nx.draw_networkx_labels(G, pos, **kwargs)

        return ax, G

    def visualize_walks(self, node_idx, edge_index, walks, edge_mask, y=None,
                        threshold=None, **kwargs) -> Tuple[Axes, nx.DiGraph]:
        r"""Visualizes the subgraph around :attr:`node_idx` given an edge mask
        :attr:`edge_mask`.

        Args:
            node_idx (int): The node id to explain.
            edge_index (LongTensor): The edge indices.
            edge_mask (Tensor): The edge mask.
            y (Tensor, optional): The ground-truth node-prediction labels used
                as node colorings. (default: :obj:`None`)
            threshold (float, optional): Sets a threshold for visualizing
                important edges. If set to :obj:`None`, will visualize all
                edges with transparancy indicating the importance of edges.
                (default: :obj:`None`)
            **kwargs (optional): Additional arguments passed to
                :func:`nx.draw`.

        :rtype: :class:`matplotlib.axes.Axes`, :class:`networkx.DiGraph`
        """
        self_loop_edge_index, _ = add_self_loops(edge_index, num_nodes=kwargs.get('num_nodes'))
        assert edge_mask.size(0) == self_loop_edge_index.size(1)

        if self.molecule:
            atomic_num = torch.clone(y)

        # Only operate on a k-hop subgraph around `node_idx`.
        subset, edge_index, _, hard_edge_mask = subgraph(
            node_idx, self.__num_hops__, self_loop_edge_index, relabel_nodes=True,
            num_nodes=None, flow=self.__flow__())

        edge_mask = edge_mask[hard_edge_mask]

        # --- temp ---
        edge_mask[edge_mask == float('inf')] = 1
        edge_mask[edge_mask == - float('inf')] = 0
        # ---

        if threshold is not None:
            edge_mask = (edge_mask >= threshold).to(torch.float)

        if y is None:
            y = torch.zeros(edge_index.max().item() + 1,
                            device=edge_index.device)
        else:
            y = y[subset]

        if self.molecule:
            atom_colors = {6: '#8c69c5', 7: '#71bcf0', 8: '#aef5f1', 9: '#bdc499', 15: '#c22f72', 16: '#f3ea19',
                           17: '#bdc499', 35: '#cc7161'}
            node_colors = [None for _ in range(y.shape[0])]
            for y_idx in range(y.shape[0]):
                node_colors[y_idx] = atom_colors[y[y_idx].int().tolist()]
        else:
            atom_colors = {0: '#8c69c5', 1: '#c56973', 2: '#a1c569', 3: '#69c5ba'}
            node_colors = [None for _ in range(y.shape[0])]
            for y_idx in range(y.shape[0]):
                node_colors[y_idx] = atom_colors[y[y_idx].int().tolist()]

        data = Data(edge_index=edge_index, att=edge_mask, y=y,
                    num_nodes=y.size(0)).to('cpu')
        G = to_networkx(data, node_attrs=['y'], edge_attrs=['att'])
        mapping = {k: i for k, i in enumerate(subset.tolist())}
        G = nx.relabel_nodes(G, mapping)

        kwargs['with_labels'] = kwargs.get('with_labels') or True
        kwargs['font_size'] = kwargs.get('font_size') or 8
        kwargs['node_size'] = kwargs.get('node_size') or 200
        kwargs['cmap'] = kwargs.get('cmap') or 'cool'

        # calculate Graph positions
        pos = nx.kamada_kawai_layout(G)
        ax = plt.gca()

        for source, target, data in G.edges(data=True):
            ax.annotate(
                '', xy=pos[target], xycoords='data', xytext=pos[source],
                textcoords='data', arrowprops=dict(
                    arrowstyle="-",
                    lw=1.5,
                    alpha=0.5,  # alpha control transparency
                    color='grey',  # color control color
                    shrinkA=sqrt(kwargs['node_size']) / 2.0,
                    shrinkB=sqrt(kwargs['node_size']) / 2.0,
                    connectionstyle="arc3,rad=0",  # rad control angle
                ))


        # --- try to draw a walk ---
        walks_ids = walks['ids']
        walks_score = walks['score']
        walks_node_list = []
        for i in range(walks_ids.shape[1]):
            if i == 0:
                walks_node_list.append(self_loop_edge_index[:, walks_ids[:, i].view(-1)].view(2, -1))
            else:
                walks_node_list.append(self_loop_edge_index[1, walks_ids[:, i].view(-1)].view(1, -1))
        walks_node_ids = torch.cat(walks_node_list, dim=0).T

        walks_mask = torch.zeros(walks_node_ids.shape, dtype=bool, device=self.device)
        for n in G.nodes():
            walks_mask = walks_mask | (walks_node_ids == n)
        walks_mask = walks_mask.sum(1) == walks_node_ids.shape[1]

        sub_walks_node_ids = walks_node_ids[walks_mask]
        sub_walks_score = walks_score[walks_mask]

        for i, walk in enumerate(sub_walks_node_ids):
            verts = [pos[n.item()] for n in walk]
            if walk.shape[0] == 3:
                codes = [Path.MOVETO, Path.CURVE3, Path.CURVE3]
            else:
                codes = [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4]
            path = Path(verts, codes)
            if sub_walks_score[i] > 0:
                patch = PathPatch(path, facecolor='none', edgecolor='red', lw=1.5,#e1442a
                                  alpha=(sub_walks_score[i] / (sub_walks_score.max() * 2)).item())
            else:
                patch = PathPatch(path, facecolor='none', edgecolor='blue', lw=1.5,#18d66b
                                  alpha=(sub_walks_score[i] / (sub_walks_score.min() * 2)).item())
            ax.add_patch(patch)


        nx.draw_networkx_nodes(G, pos, node_color=node_colors, **kwargs)
        # define node labels
        if self.molecule:
            if kwargs.get('nolabel'):
                node_labels = {n: f'{self.table(atomic_num[n].int().item())}'
                               for n in G.nodes()}
                nx.draw_networkx_labels(G, pos, labels=node_labels, **kwargs)
            else:
                node_labels = {n: f'{n}:{self.table(atomic_num[n].int().item())}'
                               for n in G.nodes()}
                nx.draw_networkx_labels(G, pos, labels=node_labels, **kwargs)
        else:
            if not kwargs.get('nolabel'):
                nx.draw_networkx_labels(G, pos, **kwargs)

        return ax, G

    



class WalkBase(ExplainerBase):

    def __init__(self, model: nn.Module, epochs=0, lr=0, explain_graph=False, molecule=False):
        super().__init__(model, epochs, lr, explain_graph, molecule)

    def extract_step(self, x, edge_index, detach=True, split_fc=False):

        layer_extractor = []
        hooks = []

        def register_hook(module: nn.Module):
            if not list(module.children()) or isinstance(module, MessagePassing) or isinstance(module, GraphConv) or isinstance(module, GraphConvolution):
                hooks.append(module.register_forward_hook(forward_hook))

        def forward_hook(module: nn.Module, input: Tuple[Tensor], output: Tensor):
            # input contains x and edge_index
            if detach:
                layer_extractor.append((module, input[0].clone().detach(), output.clone().detach()))
            else:
                layer_extractor.append((module, input[0], output))

        # --- register hooks ---
        self.model.apply(register_hook)
        pred = self.model(x, edge_index)

        for hook in hooks:
            hook.remove()


        # --- divide layer sets ---

        walk_steps = []
        fc_steps = []
        pool_flag = False
        step = {'input': None, 'module': [], 'output': None}
        for layer in layer_extractor:
            if isinstance(layer[0], MessagePassing) or isinstance(layer[0], GNNPool) or isinstance(layer[0], GraphConv) or isinstance(layer[0], GraphConvolution):
                if isinstance(layer[0], GNNPool):
                    pool_flag = True
                if step['module'] and step['input'] is not None:
                    walk_steps.append(step)
                step = {'input': layer[1], 'module': [], 'output': None}
            if pool_flag and split_fc and isinstance(layer[0], nn.Linear):
                if step['module']:
                    fc_steps.append(step)
                step = {'input': layer[1], 'module': [], 'output': None}
            step['module'].append(layer[0])
            step['output'] = layer[2]

        for walk_step in walk_steps:
            if hasattr(walk_step['module'][0], 'nn') and walk_step['module'][0].nn is not None:
                # We don't allow any outside nn during message flow process in GINs
                walk_step['module'] = [walk_step['module'][0]]
        
        if split_fc:
            if step['module']:
                fc_steps.append(step)
            return walk_steps, fc_steps
        else:
            fc_step = step

        return walk_steps, fc_step

    def walks_pick(self,
                   edge_index: Tensor,
                   pick_edge_indices: List,
                   walk_indices: List=[],
                   num_layers=0
                   ):
        walk_indices_list = []
        for edge_idx in pick_edge_indices:

            # Adding one edge
            walk_indices.append(edge_idx)
            _, new_src = src, tgt = edge_index[:, edge_idx]
            next_edge_indices = np.array((edge_index[0, :] == new_src).nonzero().view(-1))

            # Finding next edge
            if len(walk_indices) >= num_layers:
                # return one walk
                walk_indices_list.append(walk_indices.copy())
            else:
                walk_indices_list += self.walks_pick(edge_index, next_edge_indices, walk_indices, num_layers)

            # remove the last edge
            walk_indices.pop(-1)

        return walk_indices_list


    def explain_edges_with_loop(self, x: Tensor, walks: Dict[Tensor, Tensor], ex_label):

        walks_ids = walks['ids']
        walks_score = walks['score'][:walks_ids.shape[0], ex_label].reshape(-1)
        idx_ensemble = torch.cat([(walks_ids == i).int().sum(dim=1).unsqueeze(0) for i in range(self.num_edges + self.num_nodes)], dim=0)
        hard_edge_attr_mask = (idx_ensemble.sum(1) > 0).long()
        hard_edge_attr_mask_value = torch.tensor([float('inf'), 0], dtype=torch.float, device=self.device)[hard_edge_attr_mask]
        edge_attr = (idx_ensemble * (walks_score.unsqueeze(0))).sum(1)
        # idx_ensemble1 = torch.cat(
        #     [(walks_ids == i).int().sum(dim=1).unsqueeze(1) for i in range(self.num_edges + self.num_nodes)], dim=1)
        # edge_attr1 = (idx_ensemble1 * (walks_score.unsqueeze(1))).sum(0)

        return edge_attr - hard_edge_attr_mask_value

    class connect_mask(object):

        def __init__(self, cls):
            self.cls = cls

        def __enter__(self):

            self.cls.edge_mask = [nn.Parameter(torch.randn(self.cls.x_batch_size * (self.cls.num_edges + self.cls.num_nodes))) for _ in
                             range(self.cls.num_layers)] if hasattr(self.cls, 'x_batch_size') else \
                                 [nn.Parameter(torch.randn(1 * (self.cls.num_edges + self.cls.num_nodes))) for _ in
                             range(self.cls.num_layers)]

            for idx, module in enumerate(self.cls.mp_layers):
                module.__explain__ = True
                module.__edge_mask__ = self.cls.edge_mask[idx]

        def __exit__(self, *args):
            for idx, module in enumerate(self.cls.mp_layers):
                module.__explain__ = False


class GNN_GI(WalkBase):

    def __init__(self, model: nn.Module, epochs=0, lr=0, explain_graph=False, molecule=False):
        super().__init__(model=model, epochs=epochs, lr=lr, explain_graph=explain_graph, molecule=molecule)

    def forward(self,
                x: Tensor,
                edge_index: Tensor,
                args,
                **kwargs
                ):
        super().forward(x, edge_index, **kwargs)
        self.model.eval()
        self_loop_edge_index, _ = add_self_loops(edge_index, num_nodes=self.num_nodes)

        walk_steps, fc_step = self.extract_step(x, edge_index, detach=False)


        if args.explain_graph == False:
            node_idx = kwargs.get('node_idx')
            assert node_idx is not None
            _, _, _, self.hard_edge_mask = subgraph(
                node_idx, self.__num_hops__, self_loop_edge_index, relabel_nodes=True,
                num_nodes=None, flow=self.__flow__())


        def compute_walk_score(adjs, r, allow_edges, walk_idx=[]):
            if not adjs:
                walk_indices.append(walk_idx)
                walk_scores.append(r.detach())
                return
            (grads,) = torch.autograd.grad(outputs=r, inputs=adjs[0], create_graph=True)
            for i in allow_edges:
                allow_edges= torch.where(self_loop_edge_index[1] == self_loop_edge_index[0][i])[0].tolist()
                new_r = grads[i] * adjs[0][i]
                compute_walk_score(adjs[1:], new_r, allow_edges, [i] + walk_idx)


        labels = tuple(i for i in range(args.num_classes))
        walk_scores_tensor_list = [None for i in labels]
        for label in labels:

            if self.explain_graph:
                f = torch.unbind(fc_step['output'][0, label].unsqueeze(0))
                allow_edges = [i for i in range(self_loop_edge_index.shape[1])]
            else:
                f = torch.unbind(fc_step['output'][node_idx, label].unsqueeze(0))
                allow_edges = torch.where(self_loop_edge_index[1] == node_idx)[0].tolist()

            adjs = [walk_step['module'][0].edge_weight for walk_step in walk_steps]

            reverse_adjs = adjs.reverse()
            walk_indices = []
            walk_scores = []

            compute_walk_score(adjs, f, allow_edges)
            walk_scores_tensor_list[label] = torch.stack(walk_scores, dim=0).view(-1, 1)

        walks = {'ids': torch.tensor(walk_indices, device=self.device), 'score': torch.cat(walk_scores_tensor_list, dim=1)}

        # --- Apply edge mask evaluation ---
        with torch.no_grad():
            with self.connect_mask(self):
                ex_labels = tuple(torch.tensor([label]).to(args.device) for label in labels)
                masks = []
                for ex_label in ex_labels:
                    edge_attr = self.explain_edges_with_loop(x, walks, ex_label)
                    mask = edge_attr
                    mask = self.control_sparsity(mask, kwargs.get('sparsity'))
                    masks.append(mask.detach())

        return walks, masks


class GNN_LRP(WalkBase):

    def __init__(self, model: nn.Module, epochs=0, lr=0, explain_graph=False, molecule=False):
        super().__init__(model=model, epochs=epochs, lr=lr, explain_graph=explain_graph, molecule=molecule)

    def forward(self,
                x: Tensor,
                edge_index: Tensor,
                args,
                **kwargs,
                ):
        super().forward(x, edge_index, **kwargs)
        self.model.eval()

        walk_steps, fc_steps = self.extract_step(x, edge_index, detach=False, split_fc=True)


        edge_index_with_loop, _ = add_self_loops(edge_index, num_nodes=self.num_nodes)


        walk_indices_list = torch.tensor(
            self.walks_pick(edge_index_with_loop.cpu(), list(range(edge_index_with_loop.shape[1])),
                            num_layers=self.num_layers), device=self.device)
        if args.explain_graph == False:
            node_idx = kwargs.get('node_idx')
            assert node_idx is not None
            _, _, _, self.hard_edge_mask = subgraph(
                node_idx, self.__num_hops__, edge_index_with_loop, relabel_nodes=True,
                num_nodes=None, flow=self.__flow__())

            # walk indices list mask
            edge2node_idx = edge_index_with_loop[1] == node_idx
            walk_indices_list_mask = edge2node_idx[walk_indices_list[:, -1]]
            walk_indices_list = walk_indices_list[walk_indices_list_mask]


        def compute_walk_score():

            # hyper-parameter gamma
            epsilon = 1e-30   # prevent from zero division
            gamma = [2, 1, 1]

            # --- record original weights of GNN ---
            ori_gnn_weights = []
            gnn_gamma_modules = []
            clear_probe = x
            for i, walk_step in enumerate(walk_steps):
                modules = walk_step['module']
                gamma_ = gamma[i] if i <= 1 else 1
                if hasattr(modules[0], 'nn'):
                    clear_probe = modules[0](clear_probe, edge_index, probe=False)
                    # clear nodes that are not created by user
                gamma_module = copy.deepcopy(modules[0])
                if hasattr(modules[0], 'nn'):
                    for i, fc_step in enumerate(gamma_module.fc_steps):
                        fc_modules = fc_step['module']
                        if hasattr(fc_modules[0], 'weight'):
                            ori_fc_weight = fc_modules[0].weight.data
                            fc_modules[0].weight.data = ori_fc_weight + gamma_ * ori_fc_weight
                else:
                    ori_gnn_weights.append(modules[0].weight.data)
                    gamma_module.weight.data = ori_gnn_weights[i] + gamma_ * ori_gnn_weights[i].relu()
                gnn_gamma_modules.append(gamma_module)

            # --- record original weights of fc layer ---
            ori_fc_weights = []
            fc_gamma_modules = []
            for i, fc_step in enumerate(fc_steps):
                modules = fc_step['module']
                gamma_module = copy.deepcopy(modules[0])
                if hasattr(modules[0], 'weight'):
                    ori_fc_weights.append(modules[0].weight.data)
                    gamma_ = 1
                    gamma_module.weight.data = ori_fc_weights[i] + gamma_ * ori_fc_weights[i].relu()
                else:
                    ori_fc_weights.append(None)
                fc_gamma_modules.append(gamma_module)

            # --- GNN_LRP implementation ---
            for walk_indices in walk_indices_list:
                walk_node_indices = [edge_index_with_loop[0, walk_indices[0]]]
                for walk_idx in walk_indices:
                    walk_node_indices.append(edge_index_with_loop[1, walk_idx])

                h = x.requires_grad_(True)
                for i, walk_step in enumerate(walk_steps):
                    modules = walk_step['module']
                    if hasattr(modules[0], 'nn'):
                        # for the specific 2-layer nn GINs.
                        gin = modules[0]
                        run1 = gin(h, edge_index, probe=True)
                        std_h1 = gin.fc_steps[0]['output']
                        gamma_run1 = gnn_gamma_modules[i](h, edge_index, probe=True)
                        p1 = gnn_gamma_modules[i].fc_steps[0]['output']
                        q1 = (p1 + epsilon) * (std_h1 / (p1 + epsilon)).detach()

                        std_h2 = GraphSequential(*gin.fc_steps[1]['module'])(q1)
                        p2 = GraphSequential(*gnn_gamma_modules[i].fc_steps[1]['module'])(q1)
                        q2 = (p2 + epsilon) * (std_h2 / (p2 + epsilon)).detach()
                        q = q2
                    else:
                        print('GraphSequential(*modules)', GraphSequential(*modules))
                        max_n = h.size(0)
                        adj = from_edge_index_to_adj(edge_index, torch.ones(edge_index.size(1)), max_n).to(self.device)
                        #pred, adj_att = self.forward_batch(x.expand(1, -1, -1), adj.expand(1, -1, -1), batch_num_nodes, **kwargs)
                        #ypred = torch.squeeze(pred, 0)
                        std_h = GraphSequential(*modules)(h.expand(1, -1, -1), adj.expand(1, -1, -1))
                        
            
                        print('std_h', std_h)
                        print('h', h.size())
                        print('edge_index', edge_index)

                        # --- LRP-gamma ---
                        p = gnn_gamma_modules[i](h, edge_index)
                        q = (p + epsilon) * (std_h / (p + epsilon)).detach()

                    # --- pick a path ---
                    mk = torch.zeros((h.shape[0], 1), device=self.device)
                    k = walk_node_indices[i + 1]
                    mk[k] = 1
                    ht = q * mk + q.detach() * (1 - mk)
                    h = ht

                # --- FC LRP_gamma ---
                for i, fc_step in enumerate(fc_steps):
                    modules = fc_step['module']
                    std_h = nn.Sequential(*modules)(h) if i != 0 \
                        else GraphSequential(*modules)(h, torch.zeros(h.shape[0], dtype=torch.long, device=self.device))

                    # --- gamma ---
                    s = fc_gamma_modules[i](h) if i != 0 \
                        else fc_gamma_modules[i](h, torch.zeros(h.shape[0], dtype=torch.long, device=self.device))
                    ht = (s + epsilon) * (std_h / (s + epsilon)).detach()
                    h = ht

                if args.explain_graph == False:
                    f = h[node_idx, label]
                else:
                    f = h[0, label]
                x_grads = torch.autograd.grad(outputs=f, inputs=x)[0]
                I = walk_node_indices[0]
                r = x_grads[I, :] @ x[I].T
                walk_scores.append(r)


        labels = tuple(i for i in range(args.num_classes))
        walk_scores_tensor_list = [None for i in labels]
        for label in labels:

            walk_scores = []
            compute_walk_score()
            walk_scores_tensor_list[label] = torch.stack(walk_scores, dim=0).view(-1, 1)

        walks = {'ids': walk_indices_list, 'score': torch.cat(walk_scores_tensor_list, dim=1)}

        # --- Debug ---
        # walk_node_indices_list = []
        # for walk_indices in walk_indices_list:
        #     walk_node_indices = [edge_index_with_loop[0, walk_indices[0]]]
        #     for walk_idx in walk_indices:
        #         walk_node_indices.append(edge_index_with_loop[1, walk_idx])
        #     walk_node_indices_list.append(torch.stack(walk_node_indices))
        # walk_node_indices_list = torch.stack(walk_node_indices_list, dim=0)
        # --- Debug end ---

        # --- Apply edge mask evaluation ---
        with torch.no_grad():
            with self.connect_mask(self):
                ex_labels = tuple(torch.tensor([label]).to(args.device) for label in labels)
                masks = []
                for ex_label in ex_labels:
                    edge_attr = self.explain_edges_with_loop(x, walks, ex_label)
                    mask = edge_attr
                    mask = self.control_sparsity(mask, kwargs.get('sparsity'))
                    masks.append(mask.detach())

        return walks, masks

