import src.constants as constants
from src.Datasets import Definitions, batchify_defs_with_examples
from src.model import Attn_Model
from src.data_workflow import Word2Vec
import torch
from torch.autograd import Variable
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.nn import CrossEntropyLoss
from torch.nn.functional import cross_entropy
from torch.nn.utils import clip_grad_norm
from torch import from_numpy
import numpy as np
from tqdm import tqdm

# parameters

TRAIN_DATA = "./data/main_data/definitions_train.json"
VAL_DATA = "./data/main_data/definitions_val.json"
TEST_DATA = "./data/main_data/definitions_test.json"
INIT_MODEL_CKPT = "./pretrain_wiki_exp/best_pretrain"  # or None
MODEL_VOCAB = "./pretrain_wiki_exp/wiki_vocab.json"  # or None
WV_WEIGHTS = "./data/w2v_embeddings/GoogleNews-vectors-negative300.bin"
FIX_EMBEDDINGS = True
SEED = 42
CUDA = True
BATCH_SIZE = 16
NCOND = 300
NX = 300
NHID = NX + NCOND
NUM_EPOCHS = 35
NLAYERS = 3
N_ATTN_HID = 256
DROPOUT_PROB = 0.5
SEQDROPOUT_PROB = 1
SEQDROPOUT_DECAY_RATE = 1
SEQDROPOUT_DECAY_EACH = 10
INITIAL_LR = 0.001
DECAY_FACTOR = 0.1
DECAY_PATIENCE = 0
GRAD_CLIP = 5
MODEL_CKPT = "./train_def_attn_exp/best_train_attn"
RESUME = None # or None if no resume
TRAIN = True

LOGFILE = open('./train_def_attn_exp/log.txt', 'a')
EXP_RESULTS = open("./train_def_attn_exp/results.txt", "a")

# code start

np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed(SEED)

tqdm.write("Reading Data!", file=LOGFILE)
LOGFILE.flush()

defs = Definitions(
    train=TRAIN_DATA,
    val=VAL_DATA,
    test=TEST_DATA,
    with_examples=True,
    vocab_path=MODEL_VOCAB
)

if TRAIN:
    tqdm.write("Initialising Model!", file=LOGFILE)
    LOGFILE.flush()

    net = Attn_Model(
        ntokens=len(defs.vocab.i2w),
        nx=NX,
        nhid=NHID,
        ncond=NCOND,
        nlayers=NLAYERS,
        dropout=DROPOUT_PROB,
        n_attn_tokens=len(defs.cond_vocab.i2w),
        n_attn_hid=N_ATTN_HID
    )

    net.cuda()  # if cuda

    if RESUME is None:
        if INIT_MODEL_CKPT is not None:
            params = torch.load(INIT_MODEL_CKPT)
            missing = list(
                set(net.state_dict().keys()) - set(params["state_dict"])
            )
            for key in missing:
                params["state_dict"][key] = net.state_dict()[key]

            net.load_state_dict(params["state_dict"])

        tqdm.write("Doing attention init!", file=LOGFILE)
        LOGFILE.flush()
        
        w2v = Word2Vec(
            WV_WEIGHTS
        )
        
        attn_embs_init = net.attn.embs.weight.data.cpu().numpy()
        for word in defs.cond_vocab.w2i.keys():
            if word in w2v.w2i:
                cur_idx = defs.cond_vocab.w2i[word]
                attn_embs_init[cur_idx] = w2v.get_cond_vector(word)

        net.attn.embs.weight.data.copy_(torch.from_numpy(attn_embs_init))
    else:
        tqdm.write("Loading Weights for resuming training!", file=LOGFILE)
        LOGFILE.flush()
        params = torch.load(RESUME)
        net.load_state_dict(params["state_dict"])

    net.embs.weight.requires_grad = not FIX_EMBEDDINGS

    tqdm.write("Initialising Criterion and Optimizer!", file=LOGFILE)
    LOGFILE.flush()

    criterion = CrossEntropyLoss(ignore_index=constants.PAD).cuda()

    scheduler = ReduceLROnPlateau(
        torch.optim.Adam(
            filter(lambda p: p.requires_grad, net.parameters()),
            lr=INITIAL_LR
        ),
        factor=DECAY_FACTOR,
        patience=DECAY_PATIENCE
    )

    tqdm.write("Start training!", file=LOGFILE)
    LOGFILE.flush()

    min_val_loss = np.inf
    min_val_loss_idx = None
    train_losses = []
    val_losses = []

    for epoch in tqdm(range(NUM_EPOCHS), file=LOGFILE):
        LOGFILE.flush()
        ### train ###
        net.train()
        lengths_cnt = 0
        loss_i = []
        num_batches = int(
            np.ceil(
                len(defs.train) / BATCH_SIZE
            )
        )
        with tqdm(total=num_batches, file=LOGFILE) as pbar:
            train_iter = batchify_defs_with_examples(
                defs.train, defs.vocab, defs.cond_vocab, BATCH_SIZE,
                SEQDROPOUT_PROB / SEQDROPOUT_DECAY_RATE**(
                    (epoch + 1) // SEQDROPOUT_DECAY_EACH
                )
            )
            for batch_x, batch_y, conds, contexts in train_iter:
                hidden = net.init_hidden(
                    batch_x.shape[0], cuda=True
                )  # if cuda
                scheduler.optimizer.zero_grad()

                lengths = (batch_x != constants.PAD).sum(axis=1)
                maxlen = int(max(lengths))
                lengths = Variable(torch.from_numpy(lengths)).cuda().long()
                batch_x = Variable(torch.from_numpy(batch_x)).cuda()
                conds = Variable(
                    torch.from_numpy(conds)
                )
                conds = conds.cuda().long()
                batch_y = Variable(torch.from_numpy(batch_y)).cuda().view(-1)
                contexts = Variable(torch.from_numpy(contexts)).cuda().long()

                output, hidden = net(
                    batch_x, lengths, maxlen, conds, contexts, hidden
                )
                loss = criterion(output.view(-1, len(defs.vocab.i2w)), batch_y)
                loss.backward()
                clip_grad_norm(
                    filter(lambda p: p.requires_grad, net.parameters()),
                    GRAD_CLIP
                )
                scheduler.optimizer.step()

                lengths_cnt += lengths.sum().cpu().data.numpy()[0]
                loss_i.append(
                    loss.data.cpu().numpy()[0] * lengths.sum().float()
                )

                pbar.update(1)
                LOGFILE.flush()

            train_losses.append(
                np.exp(
                    np.sum(loss_i).cpu().data.numpy()[0] / lengths_cnt
                )
            )

        tqdm.write(
            "Epoch: {0}, Train PPL: {1}".format(epoch, train_losses[-1]),
            file=LOGFILE
        )
        LOGFILE.flush()

        ### val ###
        net.eval()
        loss_i = []
        lengths_cnt = 0
        num_batches = int(
            np.ceil(
                len(defs.val) / BATCH_SIZE
            )
        )
        with tqdm(total=num_batches, file=LOGFILE) as pbar:
            val_iter = batchify_defs_with_examples(
                defs.val, defs.vocab, defs.cond_vocab, BATCH_SIZE
            )
            for batch_x, batch_y, conds, contexts in val_iter:
                hidden = net.init_hidden(
                    batch_x.shape[0], cuda=True
                )  # if cuda

                lengths = (batch_x != constants.PAD).sum(axis=1)
                maxlen = int(max(lengths))
                lengths = Variable(torch.from_numpy(lengths)).cuda().long()
                batch_x = Variable(torch.from_numpy(batch_x)).cuda()
                conds = Variable(
                    torch.from_numpy(conds)
                )
                conds = conds.cuda().long()
                batch_y = Variable(torch.from_numpy(batch_y)).cuda().view(-1)
                contexts = Variable(torch.from_numpy(contexts)).cuda().long()

                output, hidden = net(
                    batch_x, lengths, maxlen, conds, contexts, hidden
                )
                loss = criterion(output.view(-1, len(defs.vocab.i2w)), batch_y)

                lengths_cnt += lengths.sum().cpu().data.numpy()[0]
                loss_i.append(
                    loss.data.cpu().numpy()[0] * lengths.sum().float()
                )

                pbar.update(1)
                LOGFILE.flush()

            val_losses.append(
                np.exp(np.sum(loss_i).cpu().data.numpy()[0] / lengths_cnt)
            )

        scheduler.step(metrics=val_losses[-1])

        tqdm.write(
            "Epoch: {0}, Val PPL: {1}".format(epoch, val_losses[-1]),
            file=LOGFILE
        )
        LOGFILE.flush()

        if val_losses[-1] < min_val_loss:
            min_val_loss = val_losses[-1]
            min_val_loss_idx = epoch
            torch.save({"state_dict": net.state_dict()}, MODEL_CKPT)

if not TRAIN:

    tqdm.write("Loading Model weights for testing!", file=LOGFILE)
    LOGFILE.flush()

    net = Attn_Model(
        ntokens=len(defs.vocab.i2w),
        nx=NX,
        nhid=NHID,
        ncond=NCOND,
        nlayers=NLAYERS,
        dropout=DROPOUT_PROB,
        n_attn_tokens=len(defs.cond_vocab.i2w),
        n_attn_hid=N_ATTN_HID
    )

    net.cuda()  # if cuda

params = torch.load(MODEL_CKPT)
net.load_state_dict(params["state_dict"])

tqdm.write("Testing...", file=LOGFILE)
LOGFILE.flush()

net.eval()

test_iter = batchify_defs_with_examples(
    defs.test, defs.vocab, defs.cond_vocab, BATCH_SIZE
)
lengths_cnt = 0
num_batches = int(
    np.ceil(
        len(defs.test) / BATCH_SIZE
    )
)
loss_i = []
with tqdm(total=num_batches, file=LOGFILE) as pbar:
    for batch_x, batch_y, conds, contexts in test_iter:
        hidden = net.init_hidden(
            batch_x.shape[0], cuda=True
        )  # if cuda

        lengths = (batch_x != constants.PAD).sum(axis=1)
        maxlen = int(max(lengths))
        lengths = Variable(torch.from_numpy(lengths)).cuda().long()
        batch_x = Variable(torch.from_numpy(batch_x)).cuda()
        conds = Variable(
            torch.from_numpy(conds)
        )
        conds = conds.cuda().long()
        batch_y = Variable(torch.from_numpy(batch_y)).cuda().view(-1)
        contexts = Variable(torch.from_numpy(contexts)).cuda().long()

        output, hidden = net(
            batch_x, lengths, maxlen, conds, contexts, hidden
        )
        loss = cross_entropy(
            output.view(-1, len(defs.vocab.i2w)),
            batch_y,
            ignore_index=constants.PAD
        )

        lengths_cnt += lengths.sum().cpu().data.numpy()[0]
        loss_i.append(loss.data.cpu().numpy()[0] * lengths.sum().float())

        pbar.update(1)
        LOGFILE.flush()

test_loss = np.exp(np.sum(loss_i).cpu().data.numpy()[0] / lengths_cnt)
tqdm.write("Test PPL: {0}".format(test_loss), file=LOGFILE)
LOGFILE.flush()
LOGFILE.close()

tqdm.write("Parameters:\n", file=EXP_RESULTS)
tqdm.write("TRAIN_DATA = {0}".format(TRAIN_DATA), file=EXP_RESULTS)
tqdm.write("VAL_DATA = {0}".format(VAL_DATA), file=EXP_RESULTS)
tqdm.write("TEST_DATA = {0}".format(TEST_DATA), file=EXP_RESULTS)
tqdm.write("INIT_MODEL_CKPT = {0}".format(INIT_MODEL_CKPT), file=EXP_RESULTS)
tqdm.write("MODEL_VOCAB = {0}".format(MODEL_VOCAB), file=EXP_RESULTS)
tqdm.write("WV_WEIGHTS = {0}".format(WV_WEIGHTS), file=EXP_RESULTS)
tqdm.write("FIX_EMBEDDINGS = {0}".format(FIX_EMBEDDINGS), file=EXP_RESULTS)
tqdm.write("SEED = {0}".format(SEED), file=EXP_RESULTS)
tqdm.write("CUDA = {0}".format(CUDA), file=EXP_RESULTS)
tqdm.write("BATCH_SIZE = {0}".format(BATCH_SIZE), file=EXP_RESULTS)
tqdm.write("NCOND = {0}".format(NCOND), file=EXP_RESULTS)
tqdm.write("NX = {0}".format(NX), file=EXP_RESULTS)
tqdm.write("NHID = {0}".format(NCOND), file=EXP_RESULTS)
tqdm.write("NUM_EPOCHS = {0}".format(NUM_EPOCHS), file=EXP_RESULTS)
tqdm.write("NLAYERS = {0}".format(NLAYERS), file=EXP_RESULTS)
tqdm.write("N_ATTN_HID = {0}".format(N_ATTN_HID), file=EXP_RESULTS)
tqdm.write("DROPOUT_PROB = {0}".format(DROPOUT_PROB), file=EXP_RESULTS)
tqdm.write("SEQDROPOUT_PROB = {0}".format(SEQDROPOUT_PROB), file=EXP_RESULTS)
tqdm.write(
    "SEQDROPOUT_DECAY_RATE = {0}".format(SEQDROPOUT_DECAY_RATE),
    file=EXP_RESULTS
)
tqdm.write(
    "SEQDROPOUT_DECAY_EACH = {0}".format(SEQDROPOUT_DECAY_EACH),
    file=EXP_RESULTS
)
tqdm.write("INITIAL_LR = {0}".format(INITIAL_LR), file=EXP_RESULTS)
tqdm.write("DECAY_FACTOR = {0}".format(DECAY_FACTOR), file=EXP_RESULTS)
tqdm.write("DECAY PATIENCE = {0}".format(DECAY_PATIENCE), file=EXP_RESULTS)
tqdm.write("GRAD_CLIP = {0}".format(GRAD_CLIP), file=EXP_RESULTS)
tqdm.write("MODEL_CKPT = {0}".format(MODEL_CKPT), file=EXP_RESULTS)
tqdm.write("RESUME = {0}".format(RESUME), file=EXP_RESULTS)
tqdm.write("TRAIN = {0}\n\n".format(TRAIN), file=EXP_RESULTS)
tqdm.write("RESULTS:\n", file=EXP_RESULTS)
if TRAIN:
    tqdm.write("TRAIN PPL: {0}".format(
        train_losses[min_val_loss_idx]), file=EXP_RESULTS
    )
    tqdm.write("VAL PPL: {0}".format(min_val_loss), file=EXP_RESULTS)
tqdm.write("TEST PPL: {0}".format(test_loss), file=EXP_RESULTS)

EXP_RESULTS.flush()
EXP_RESULTS.close()
