import networkx as nx
import numpy as np
import torch
from sklearn.model_selection import train_test_split
from torch_geometric.utils import from_networkx

from dataset.syn_utils.gengraph import *


def build_syndata(args):
    """Generate synthetic graohs and convert them into Pytorch geometric Data object.

    Returns:
        Data: converted synthetic Pytorch geometric Data object
    """
    generate_function = "gen_" + args.dataset

    G, labels, name = eval(generate_function)(
        nb_shapes=args.num_shapes,
        width_basis=args.width_basis,
        feature_generator=featgen.ConstFeatureGen(np.ones(args.input_dim, dtype=float)),
    )

    data = from_networkx(G.to_undirected(), all)
    data.adj = torch.LongTensor(nx.to_numpy_matrix(G))
    data.num_classes = len(np.unique(labels))
    data.y = torch.LongTensor(labels)
    data.x = data.x.float()
    data.edge_weight = torch.ones(data.edge_index.size(1))
    n = data.num_nodes
    data.train_mask, data.val_mask, data.test_mask = (
        torch.zeros(n, dtype=torch.bool),
        torch.zeros(n, dtype=torch.bool),
        torch.zeros(n, dtype=torch.bool),
    )
    train_ids, test_ids = train_test_split(range(n), test_size=args.test_ratio, random_state=args.seed, shuffle=True)
    train_ids, val_ids = train_test_split(train_ids, test_size=args.val_ratio, random_state=args.seed, shuffle=True)

    data.train_mask[train_ids] = 1
    data.val_mask[val_ids] = 1
    data.test_mask[test_ids] = 1

    return data
