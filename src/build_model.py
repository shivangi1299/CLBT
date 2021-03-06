# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
#

import logging
import torch
from torch import nn

from src.utils import load_embeddings, normalize_embeddings
from src.bert_modeling import BertConfig, BertModel
from src.maps import NonLinearMap, SelfAttentionMap, AttentionMap, LinearSelfAttentionMap, NonLinearSelfAttentionMap

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s', 
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)

class Discriminator(nn.Module):

    def __init__(self, args, bert_hidden_size):
        super(Discriminator, self).__init__()

        self.emb_dim = bert_hidden_size
        self.dis_layers = args.dis_layers
        self.dis_hid_dim = args.dis_hid_dim
        self.dis_dropout = args.dis_dropout
        self.dis_input_dropout = args.dis_input_dropout

        layers = [nn.Dropout(self.dis_input_dropout)]
        for i in range(self.dis_layers + 1):
            input_dim = self.emb_dim if i == 0 else self.dis_hid_dim
            output_dim = 1 if i == self.dis_layers else self.dis_hid_dim
            layers.append(nn.Linear(input_dim, output_dim))
            if i < self.dis_layers:
                layers.append(nn.LeakyReLU(0.2))
                layers.append(nn.Dropout(self.dis_dropout))
        layers.append(nn.Sigmoid())
        self.layers = nn.Sequential(*layers)

    def forward(self, x):
        assert x.dim() == 2 and x.size(1) == self.emb_dim
        return self.layers(x).view(-1)


def build_model(args, with_dis):
    """
    Build all components of the model.
    """

    if args.local_rank == -1 or args.no_cuda:
        device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
        n_gpu = torch.cuda.device_count()
    else:
        device = torch.device("cuda", args.local_rank)
        n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend='nccl')
    print("device", device, "n_gpu", n_gpu, "distributed training", bool(args.local_rank != -1))

    
    if args.load_pred_bert:
        model = None
        model1 = None
    else:
        bert_config = BertConfig.from_json_file(args.bert_config_file)
        model = BertModel(bert_config)
        if args.init_checkpoint is not None:
            model.load_state_dict(torch.load(args.init_checkpoint, map_location='cpu'))
        model.to(device)

        if args.bert_config_file1: 
            bert_config1 = BertConfig.from_json_file(args.bert_config_file1)
        model1 = BertModel(bert_config1)
        if args.init_checkpoint is not None:
            model1.load_state_dict(torch.load(args.init_checkpoint1, map_location='cpu'))
        model1.to(device)
        assert bert_config.hidden_size == bert_config1.hidden_size

    # mapping
    #if args.non_linear:
    if args.map_type == 'nonlinear':
        #assert args.emb_dim == bert_config.hidden_size
        mapping = NonLinearMap(args)
    #elif args.transformer:
    elif args.map_type == 'self_attention':
        mapping = SelfAttentionMap(args)
    elif args.map_type == 'attention':
        mapping = AttentionMap(args)
    elif args.map_type == 'linear_self_attention':
        mapping = LinearSelfAttentionMap(args)
    elif args.map_type == 'nonlinear_self_attention':
        mapping = NonLinearSelfAttentionMap(args)
    elif args.map_type == 'fine_tune':
        mapping = None
    elif args.map_type == 'linear' or args.map_type == 'svd':
        #assert args.emb_dim == bert_config.hidden_size
        logger.info("Linear mapping:\nEmbedding Dimension:{}".format(args.emb_dim))
        mapping = nn.Linear(args.emb_dim, args.emb_dim, bias=False)
        if getattr(args, 'map_id_init', True):
            mapping.weight.data.copy_(torch.diag(torch.ones(args.emb_dim)))
    else:
        raise ValueError("Invalid map type: {}".format(args.map_type))
        exit(1)
    if mapping:
        mapping.to(device)

    if args.local_rank != -1 and not args.load_pred_bert:
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.local_rank],
                                                          output_device=args.local_rank)
    elif n_gpu > 1:
        if not args.load_pred_bert:
            model = torch.nn.DataParallel(model)
            model1 = torch.nn.DataParallel(model1)
        if mapping:
            mapping = torch.nn.DataParallel(mapping)

    return model, model1, mapping
