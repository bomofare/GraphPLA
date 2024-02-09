import torch
import torch.nn as nn
import numpy as np
from tqdm import tqdm
import metrics
import torch.nn.functional as F
from torch_geometric.nn import GCNConv,GATConv
from torch_geometric.nn import global_max_pool as gmp
from torch.utils.data import DataLoader

from dataset import PT_FEATURE_SIZE

CHAR_SMI_SET_LEN = 64
torch.set_printoptions(profile="full")

class Squeeze(nn.Module):
    def forward(self, input: torch.Tensor):
        return input.squeeze()


class CDilated(nn.Module):
    def __init__(self, nIn, nOut, kSize, stride=1, d=1):
        super().__init__()
        padding = int((kSize - 1) / 2) * d
        self.conv = nn.Conv1d(nIn, nOut, kSize, stride=stride, padding=padding, bias=False, dilation=d)

    def forward(self, input):
        output = self.conv(input)
        return output


class DilatedParllelResidualBlockA(nn.Module):
    #128    32 64 64 128
    def __init__(self, nIn, nOut, add=True):
        super().__init__()
        n = int(nOut / 5)
        n1 = nOut - 4 * n
        self.c1 = nn.Conv1d(nIn, n, 1, padding=0)
        self.br1 = nn.Sequential(nn.BatchNorm1d(n), nn.PReLU())
        self.d1 = CDilated(n, n1, 3, 1, 1)  # dilation rate of 2^0
        self.d2 = CDilated(n, n, 3, 1, 2)  # dilation rate of 2^1
        self.d4 = CDilated(n, n, 3, 1, 4)  # dilation rate of 2^2
        self.d8 = CDilated(n, n, 3, 1, 8)  # dilation rate of 2^3
        self.d16 = CDilated(n, n, 3, 1, 16)  # dilation rate of 2^4
        self.br2 = nn.Sequential(nn.BatchNorm1d(nOut), nn.PReLU())

        if nIn != nOut:
            #             print(f'{nIn}-{nOut}: add=False')
            add = False
        self.add = add


    def forward(self, input):
        # reduce
        output1 = self.c1(input)
        output1 = self.br1(output1)
        # split and transform
        d1 = self.d1(output1)
        d2 = self.d2(output1)
        d4 = self.d4(output1)
        d8 = self.d8(output1)
        d16 = self.d16(output1)

        # heirarchical fusion for de-gridding
        add1 = d2
        add2 = add1 + d4
        add3 = add2 + d8
        add4 = add3 + d16

        # merge
        combine = torch.cat([d1, add1, add2, add3, add4], 1)

        # if residual version
        if self.add:
            combine = input + combine
        output = self.br2(combine)
        return output


class DeepDTAF(nn.Module):

    def __init__(self):
        super().__init__()

        smi_embed_size = 128
        seq_embed_size = 128

        seq_oc = 128
        pkt_oc = 128
        smi_oc = 128

        num_features_xd = 78

        dropout = 0.2

        self.smi_embed = nn.Embedding(CHAR_SMI_SET_LEN, smi_embed_size)

        self.seq_embed = nn.Linear(PT_FEATURE_SIZE, seq_embed_size)  # (N, *, H_{in}) -> (N, *, H_{out})

        self.pkt_embed = nn.Linear(PT_FEATURE_SIZE, seq_embed_size)  # (N, *, H_{in}) -> (N, *, H_{out})

        conv_seq = []
        ic = seq_embed_size
        for oc in [32, 64, 64, seq_oc]:
            conv_seq.append(DilatedParllelResidualBlockA(ic, oc))
            ic = oc
        conv_seq.append(nn.AdaptiveMaxPool1d(1))  # (N, oc)
        conv_seq.append(Squeeze())
        self.conv_seq = nn.Sequential(*conv_seq)

        # (N, H=32, L)
        conv_pkt = []
        ic = seq_embed_size
        for oc in [32, 64, pkt_oc]:
            conv_pkt.append(nn.Conv1d(ic, oc, 3))  # (N,C,L)
            conv_pkt.append(nn.BatchNorm1d(oc))
            conv_pkt.append(nn.PReLU())
            ic = oc
        conv_pkt.append(nn.AdaptiveMaxPool1d(1))
        conv_pkt.append(Squeeze())
        self.conv_pkt = nn.Sequential(*conv_pkt)  # (N,oc)

        # TUDO 修改smile的处理部分
        self.gat1 = GATConv(num_features_xd, num_features_xd, heads=4, dropout=dropout)
        self.gat2 = GATConv(num_features_xd * 4, num_features_xd, heads=4,dropout=dropout)
        self.gat3 = GATConv(num_features_xd * 4, num_features_xd, heads=4,dropout=dropout)
        self.gat4 = GATConv(num_features_xd * 4, num_features_xd, heads=4,dropout=dropout)
        self.gat5 = GATConv(num_features_xd * 4, num_features_xd,dropout=dropout)
        self.gcn = GCNConv(num_features_xd , num_features_xd)
        self.fc_g1 = nn.Linear(smi_oc, smi_oc)

        self.cat_dropout = nn.Dropout(0.2)
        self.relu = nn.PReLU()

        self.BatchNorm1d = nn.BatchNorm1d(128)
        self.BatchNorm1d1 = nn.BatchNorm1d(312)
        self.classifier = nn.Sequential(
            nn.Linear(seq_oc + pkt_oc + smi_oc, 128),
            nn.Dropout(0.2),
            nn.PReLU(),
            nn.Linear(128, 64),
            nn.Dropout(0.2),
            nn.PReLU(),
            nn.Linear(64, 1),
            nn.PReLU())

    def forward(self, seq, pkt, data):
        # print('seq', seq)
        # print('pkt', pkt)
        # print('smi', smi)
        # print('data', data)
        # assert seq.shape == (N,L,43)
        seq_embed = self.seq_embed(seq)  # (N,L,32)
        seq_embed = torch.transpose(seq_embed, 1, 2)  # (N,32,L)
        seq_conv = self.conv_seq(seq_embed)  # (N,128)

        # assert pkt.shape == (N,L,43)
        pkt_embed = self.pkt_embed(pkt)  # (N,L,32)
        pkt_embed = torch.transpose(pkt_embed, 1, 2)
        pkt_conv = self.conv_pkt(pkt_embed)  # (N,128)

        # graph input feed-forward
        smi_conv, edge_index, batch = data.x, data.edge_index, data.batch
        # print('1')

        # assert smi.shape == (N, L)

        smi_conv = F.dropout(smi_conv, p=0.2, training=self.training)
        smi_conv = self.gat1(smi_conv, edge_index)
        smi_conv = self.BatchNorm1d1(smi_conv)
        smi_conv = F.elu(smi_conv)
        # smi_conv = F.elu(self.gat1(smi_conv, edge_index))
        smi_conv = F.dropout(smi_conv, p=0.2, training=self.training)
        smi_conv = self.gat2(smi_conv, edge_index)
        smi_conv = self.BatchNorm1d1(smi_conv)
        smi_conv = F.elu(smi_conv)
        # smi_conv = F.elu(self.gat2(smi_conv, edge_index))
        smi_conv = F.dropout(smi_conv, p=0.2, training=self.training)
        smi_conv = self.gat3(smi_conv, edge_index)
        smi_conv = self.BatchNorm1d1(smi_conv)
        smi_conv = F.elu(smi_conv)
        # smi_conv = F.elu(self.gat3(smi_conv, edge_index))
        smi_conv = F.dropout(smi_conv, p=0.2, training=self.training)
        smi_conv = self.gat4(smi_conv, edge_index)
        smi_conv = self.BatchNorm1d1(smi_conv)
        smi_conv = F.elu(smi_conv)
        # smi_conv = F.elu(self.gat4(smi_conv, edge_index))
        smi_conv = F.dropout(smi_conv, p=0.2, training=self.training)
        smi_conv = self.gat5(smi_conv, edge_index)
        smi_conv = self.BatchNorm1d(smi_conv)
        smi_conv = F.elu(smi_conv)
        # smi_conv = F.elu(self.gat4(smi_conv, edge_index))
        smi_conv = F.dropout(smi_conv, p=0.2, training=self.training)
        smi_conv = self.gcn(smi_conv, edge_index)
        smi_conv = self.BatchNorm1d(smi_conv)
        smi_conv = F.elu(smi_conv)
        smi_conv = gmp(smi_conv, batch)  # global max pooling
        smi_conv = self.fc_g1(smi_conv)
        smi_conv = self.relu(smi_conv)


        # print('seq_conv', seq_conv)
        # print('pkt_conv', pkt_conv)
        # print('smi_conv', smi_conv)
        # print('len(seq_conv.shape)', len(seq_conv.shape))
        # print('len(pkt_conv.shape)', len(pkt_conv.shape))
        # print('len(smi_conv.shape)', len(smi_conv.shape))
        if len(pkt_conv.shape) == 1:
            smi_conv = torch.squeeze(smi_conv,0)
            # print('smi_conv', smi_conv)
            cat = torch.cat([seq_conv, pkt_conv, smi_conv], dim=0)  # (N,128*3)
        else:
            cat = torch.cat([seq_conv, pkt_conv, smi_conv], dim=1)  # (N,128*3)

        cat = self.cat_dropout(cat)

        output = self.classifier(cat)
        return output


def test(model: nn.Module, test_loader,datas, loss_function, device, show):
    model.eval()
    test_loss = 0
    outputs = []
    targets = []
    with torch.no_grad():
        for idx, (*x, y) in tqdm(enumerate(test_loader), disable=not show, total=len(test_loader)):
            for batch_idx, data in enumerate(datas):
                if idx == batch_idx:
                    for i in range(len(x)):
                        x[i] = x[i].to(device)
                    y = y.to(device)
                    data = data.to(device)
                    y_hat = model(*x, data)

                    test_loss += loss_function(y_hat.view(-1), y.view(-1)).item()
                    outputs.append(y_hat.cpu().numpy().reshape(-1))
                    targets.append(y.cpu().numpy().reshape(-1))

    targets = np.concatenate(targets).reshape(-1)
    outputs = np.concatenate(outputs).reshape(-1)

    test_loss /= len(test_loader.dataset)

    evaluation = {
        'loss': test_loss,
        'c_index': metrics.c_index(targets, outputs),
        'RMSE': metrics.RMSE(targets, outputs),
        'MAE': metrics.MAE(targets, outputs),
        'SD': metrics.SD(targets, outputs),
        'CORR': metrics.CORR(targets, outputs),
    }

    return evaluation
