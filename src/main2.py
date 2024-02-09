import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from torch import nn, optim
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from tqdm.auto import tqdm
from apex import amp  # uncomment lines related with `amp` to use apex

from dataset import MyDataset
from model import DeepDTAF, test

from rdkit import Chem
from rdkit.Chem import MolFromSmiles
import networkx as nx
import pandas as pd
from utils import TestbedDataset
from torch_geometric import data as DATA
from torch_geometric.loader import DataLoader as tgDataLoader


#add


def atom_features(atom):
    return np.array(one_of_k_encoding_unk(atom.GetSymbol(),
                                          ['C', 'N', 'O', 'S', 'F', 'Si', 'P', 'Cl', 'Br', 'Mg', 'Na', 'Ca', 'Fe', 'As',
                                           'Al', 'I', 'B', 'V', 'K', 'Tl', 'Yb', 'Sb', 'Sn', 'Ag', 'Pd', 'Co', 'Se',
                                           'Ti', 'Zn', 'H', 'Li', 'Ge', 'Cu', 'Au', 'Ni', 'Cd', 'In', 'Mn', 'Zr', 'Cr',
                                           'Pt', 'Hg', 'Pb', 'Unknown']) +
                    one_of_k_encoding(atom.GetDegree(), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) +
                    one_of_k_encoding_unk(atom.GetTotalNumHs(), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) +
                    one_of_k_encoding_unk(atom.GetImplicitValence(), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) +
                    [atom.GetIsAromatic()])


def one_of_k_encoding(x, allowable_set):
    if x not in allowable_set:
        raise Exception("input {0} not in allowable set{1}:".format(x, allowable_set))
    return list(map(lambda s: x == s, allowable_set))


def one_of_k_encoding_unk(x, allowable_set):
    """Maps inputs not in the allowable set to the last element."""
    if x not in allowable_set:
        x = allowable_set[-1]
    return list(map(lambda s: x == s, allowable_set))


def smile_to_graph(mol):
    # mol2 = Chem.MolFromSmiles(smile)
    c_size = mol.GetNumAtoms()

    features = []
    for atom in mol.GetAtoms():
        feature = atom_features(atom)
        features.append(feature / sum(feature))

    edges = []
    # print('mol2',mol2.GetBonds())
    # print('smile', smile)
    bonds = mol.GetBonds()
    for bond in bonds:
        # print('1111111')
        edges.append([bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()])
    g = nx.Graph(edges).to_directed()
    edge_index = []
    for e1, e2 in g.edges:
        edge_index.append([e1, e2])
    # print(c_size)
    # print(features)
    # print(edge_index)

    return c_size, features, edge_index
    # add

#
#
# BOND_TYPE = [0,
#              Chem.rdchem.BondType.SINGLE,
#              Chem.rdchem.BondType.DOUBLE,
#              Chem.rdchem.BondType.TRIPLE,
#              Chem.rdchem.BondType.QUADRUPLE]
#
# VALENCE_CHECK = {'Na': 1, 'Li': 1, 'K': 1, 'Mg': 2, 'B': 3, 'C': 4}
# MAX_BONDS = {'C': 4, 'N': 3, 'O': 2, 'Br': 1, 'Cl': 1, 'F': 1, 'S': 6, 'Sn': 4}
# MAX_BONDS.update(VALENCE_CHECK)


# def fix_endpoint(atom, verbose=False):
#     explicit_bonds = int(sum([b.GetBondTypeAsDouble() for b in atom.GetBonds()]))
#     explicit_hs = atom.GetNumExplicitHs()
#     atom_symbol = atom.GetSymbol()
#     charge = atom.GetFormalCharge()
#     print("Atom is", atom_symbol, "with",
#           charge, "charge,",
#           explicit_bonds, "bonds,",
#           explicit_hs, "explicit Hs")
#     print(atom_symbol)
#     print(MAX_BONDS[atom_symbol])
#     print(explicit_bonds)
#     print(explicit_hs)
#
#     if verbose:
#         print("Atom is", atom_symbol, "with",
#               charge, "charge,",
#               explicit_bonds, "bonds,",
#               explicit_hs, "explicit Hs")
#
#     while atom_symbol in MAX_BONDS and explicit_bonds + explicit_hs > MAX_BONDS[atom_symbol]:
#         if explicit_hs > 0:
#             explicit_hs -= 1
#             atom.SetNumExplicitHs(explicit_hs)
#         else:
#             charge += 1
#             atom.SetFormalCharge(charge)
#         explicit_bonds = int(sum([b.GetBondTypeAsDouble() for b in atom.GetBonds()]))
#         explicit_hs = atom.GetNumExplicitHs()
#         atom_symbol = atom.GetSymbol()
#         charge = atom.GetFormalCharge()
#
#
#     # Valence constraint isn't exactly the same as the max bonds constraint...
#     while atom_symbol in VALENCE_CHECK and charge + explicit_hs + explicit_bonds > VALENCE_CHECK[atom_symbol]:
#
#         if explicit_hs > 0:
#             # if there are hydrogens, drop them
#             explicit_hs -= 1
#             atom.SetNumExplicitHs(explicit_hs)
#         elif charge > 0:
#             charge -= 1  # remove an electron by stripping charge
#             atom.SetFormalCharge(charge)
#         explicit_bonds = int(sum([b.GetBondTypeAsDouble() for b in atom.GetBonds()]))
#         explicit_hs = atom.GetNumExplicitHs()
#         atom_symbol = atom.GetSymbol()
#         charge = atom.GetFormalCharge()
#
#     if verbose:
#         print("Atom is", atom_symbol, "with",
#               charge, "charge,",
#               explicit_bonds, "bonds,",
#               explicit_hs, "explicit Hs")



if __name__ == '__main__':
    print(sys.argv)

    # ↓↓↓↓↓↓↓↓↓↓↓↓↓↓
    SHOW_PROCESS_BAR = True
    data_path = '../data/'
    seed = np.random.randint(33927, 33928)  ##random
    path = Path(f'../runs/DeepDTAF_{datetime.now().strftime("%Y%m%d%H%M%S")}_{seed}')
    device = torch.device("cuda:0")  # or torch.device('cpu')

    max_seq_len = 1000
    max_pkt_len = 63
    max_smi_len = 150

    batch_size = 512
    n_epoch = 100
    # n_epoch = 40
    # n_epoch = 5
    interrupt = None
    save_best_epoch = 1  # when `save_best_epoch` is reached and the loss starts to decrease, save best model parameters
    # save_best_epoch = 3
    # ↑↑↑↑↑↑↑↑↑↑↑↑↑↑

    # GPU uses cudnn as backend to ensure repeatable by setting the following (in turn, use advances function to speed up training)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    torch.manual_seed(seed)
    np.random.seed(seed)

    writer = SummaryWriter(path)
    f_param = open(path / 'parameters.txt', 'w')

    print(f'device={device}')
    print(f'seed={seed}')
    print(f'write to {path}')
    f_param.write(f'device={device}\n'
                  f'seed={seed}\n'
                  f'write to {path}\n')

    print(f'max_seq_len={max_seq_len}\n'
          f'max_pkt_len={max_pkt_len}\n'
          f'max_smi_len={max_smi_len}')

    f_param.write(f'max_seq_len={max_seq_len}\n'
                  f'max_pkt_len={max_pkt_len}\n'
                  f'max_smi_len={max_smi_len}\n')

    assert 0 < save_best_epoch < n_epoch
    # TUDO 修改模型
    model = DeepDTAF()
    model = model.to(device)
    print(model)
    f_param.write('model: \n')
    f_param.write(str(model) + '\n')
    f_param.close()

    # add
    datas = {}
    pdbdata = {}
    pdbsdata = {}
    for phase in ['training', 'validation', 'test']:
        print('__________________________________________________')
        print('__________________________________________________')
        print('__________________________________________________')
        print(phase)
        data_path = Path(data_path)
        smiles = []
        df = pd.read_csv(data_path / f"{phase}_mol.csv")
        smiles = list(df['smiles'])
        pdbs = list(df['pdbid'])
        compound_iso_smiles = []
        deal_pdb = []
        smile_graph = {}
        for index in range(len(smiles)):
            smile = smiles[index]
            pdb = pdbs[index]
            # mol2 = Chem.MolFromSmiles(smile, sanitize=True)
            mol = Chem.MolFromMolFile('../data/' + phase + '/sdfchange/' + pdb + '.sdf')
            # if mol is None:
            #     print('0')
            #     mol = Chem.MolFromMolFile('../data/' + phase + '/mol/' + pdb + '.mol', sanitize=True, removeHs=True)
            #     if mol is None:
            #         mol = Chem.MolFromMol2File('../data/' + phase + '/mol2/' + pdb + '.mol2', sanitize=True,
            #                                    removeHs=True)
            #         print('1')
            #         if mol is None:
            #             print('2')
            #             mol = Chem.MolFromSmiles(smile, sanitize=True)
            #             if mol is None:
            #                 continue
                            # mol = Chem.MolFromSmiles(smile, sanitize=False)
                            # print('3')
                            # print('mol', mol)
                            # for atom in mol.GetAtoms():
                            #     print('atom', atom)
                            #     print('4')
                            #     fix_endpoint(atom)
            # print(mol)
            # print(pdb)
            deal_pdb.append(pdbs[index])
            g = smile_to_graph(mol)
            smile_graph[smile] = g
            compound_iso_smiles.append(smile)
            # compound_iso_smile = Chem.MolToSmiles(mol2, isomericSmiles=True)
            # compound_iso_smiles.append(compound_iso_smile)
            # if not mol is None:
            #     compound_iso_smile = Chem.MolToSmiles(mol, isomericSmiles=True)
            #     compound_iso_smiles.append(compound_iso_smile)
            #     deal_pdb.append(pdbs[index])

        # smile_graph = {}
        # for smile in compound_iso_smiles:
        #     g = smile_to_graph(smile)
        #     smile_graph[smile] = g
        train_data = TestbedDataset(root='data', dataset=phase, compound_iso_smiles=compound_iso_smiles,
                                    smile_graph=smile_graph)
        train_loader = tgDataLoader(train_data, batch_size=batch_size, shuffle=False)
        # print(format(len(train_loader.dataset)))
        # print(len(train_loader))
        # print('train_loader', train_loader)
        # print('train_data', train_data)
        # print('Training on {} samples...'.format(len(train_loader.dataset)))
        # print(len(train_loader))
        # for data in train_loader:
        #     print('data', data)
        # test_loader = DataLoader(data['test'], batch_size=512, shuffle=False)
        # validation_loader = DataLoader(data['validation'], batch_size=512, shuffle=False)
        # print('self.train_loader', train_loader123)
        datas[phase] = train_loader
        pdbdata[phase] = deal_pdb
        pdbsdata[phase] = pdbs
    # print('train_loader',len(pdbdata['training']))
    # print('train_loader',len(pdbdata['validation']))
    # print('train_loader',len(pdbdata['test']))
    # print(aca)


    # print('self.processed_paths[0]',self.abcd[0])
    # add

    data_loaders = {phase_name:
                        DataLoader(MyDataset(data_path, phase_name,pdbdata,
                                             max_seq_len, max_pkt_len, max_smi_len, pkt_window=None),
                                   batch_size=batch_size,
                                   pin_memory=True,
                                   num_workers=8,
                                   shuffle=True)
                    for phase_name in ['training', 'validation', 'test']}
    # print(asd)
    optimizer = optim.AdamW(model.parameters(), lr=0.0005)
    scheduler = optim.lr_scheduler.OneCycleLR(optimizer, max_lr=5e-3, epochs=n_epoch,
                                              steps_per_epoch=len(data_loaders['training']))
    schedulerdown = optim.lr_scheduler.MultiStepLR(optimizer, milestones=[5, 10, 15], gamma=0.1, last_epoch=-1)

    loss_function = nn.MSELoss(reduction='sum')

    # fp16
    model, optimizer = amp.initialize(model, optimizer, opt_level="O1")

    start = datetime.now()
    print('start at ', start)
    print('****************************************************** ')
    # print('model ', model)
    best_epoch = -1
    best_val_loss = 100000000
    for epoch in range(1, n_epoch + 1):
        print('epoch---------------------------',epoch)
        # print(format(len(data_loaders['training'].dataset)))
        # print(len(data_loaders['training']))
        tbar = tqdm(enumerate(data_loaders['training']), disable=not SHOW_PROCESS_BAR,
                    total=len(data_loaders['training']))
        for idx, (*x, y) in tbar:
            for batch_idx, data in enumerate(datas['training']):
                if idx == batch_idx:
                    model.train()

                    for i in range(len(x)):
                        x[i] = x[i].to(device)


                    y = y.to(device)
                    data = data.to(device)
                    # print('*x-=-=-=-=-', *x)
                    # print('y-=-==-=-=-', y)

                    optimizer.zero_grad()

                    output = model(*x, data)
                    print('output', output)
                    print('y', y.view(-1))
                    loss = loss_function(output.view(-1), y.view(-1))


                    print('loss', loss)

                    # fp16
                    with amp.scale_loss(loss, optimizer) as scaled_loss:
                        scaled_loss.backward()
                    #         loss.backward()

                    optimizer.step()
                    scheduler.step()

                    tbar.set_description(f' * Train Epoch {epoch} Loss={loss.item() / len(y):.3f}')

        for _p in ['training', 'validation']:
            print(f'{_p}:')
            performance = test(model, data_loaders[_p],datas[_p], loss_function, device, SHOW_PROCESS_BAR)
            for i in performance:
                writer.add_scalar(f'{_p} {i}', performance[i], global_step=epoch)
            if _p == 'validation' and epoch >= save_best_epoch and performance['loss'] < best_val_loss:
                best_val_loss = performance['loss']
                best_epoch = epoch
                torch.save(model.state_dict(), path / 'best_model.pt')

        schedulerdown.step()

    model.load_state_dict(torch.load(path / 'best_model.pt'))
    with open(path / 'result.txt', 'w') as f:
        f.write(f'best model found at epoch NO.{best_epoch}\n')
        for _p in ['training', 'validation', 'test', ]:
            performance = test(model, data_loaders[_p],datas[_p], loss_function, device, SHOW_PROCESS_BAR)
            f.write(f'{_p}:\n')
            print(f'{_p}:')
            for k, v in performance.items():
                f.write(f'{k}: {v}\n')
                print(f'{k}: {v}\n')
            f.write('\n')
            print()

    print('training finished')

    end = datetime.now()
    print('end at:', end)
    print('time used:', str(end - start))



