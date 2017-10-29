import constants
import json
import numpy as np


def pad(seq, size):
    if len(seq) < size:
        seq.extend([constants.PAD] * (size - len(seq)))
    return seq


def batchify(data, vocab, seqlen, batch_size):
    i = 0
    while i < len(data):
        batch_x_i = []
        batch_y_i = []
        maxlen = -np.inf
        for j in range(batch_size):
            batch_x_i.append(vocab.encode_seq(data[i:i + seqlen]))
            batch_y_i.append(vocab.encode_seq(data[i + 1:i + seqlen + 1]))
            maxlen = max(maxlen, len(batch_x_i[-1]), len(batch_y_i[-1]))
            i = i + seqlen + 1
            if i >= len(data):
                break
        for j in range(len(batch_x_i)):
            batch_x_i[j] = pad(batch_x_i[j], maxlen)
            batch_y_i[j] = pad(batch_y_i[j], maxlen)
        yield np.array(batch_x_i), np.array(batch_y_i)


def batchify_defs(data, vocab, cond_wv, batch_size):
    i = 0
    while i < len(data):
        batch_x_i = []
        batch_y_i = []
        batch_cond_i = []
        lengths = []
        for j in range(batch_size):
            batch_x_i.append(
                vocab.encode_seq(data[i][0] + data[i][1])
            )
            batch_y_i.append(
                vocab.encode_seq(data[i][1] + [constants.EOS_WORD])
            )
            batch_cond_i.append(
                cond_wv.get_cond_vector(data[i][0][0], context=data[i][1])
            )
            lengths.append(len(batch_x_i[-1]))

            i += 1
            if i >= len(data):
                break

        maxlen = max(lengths)
        for j in range(len(batch_x_i)):
            batch_x_i[j] = pad(batch_x_i[j], maxlen)
            batch_y_i[j] = pad(batch_y_i[j], maxlen)

        order = np.argsort(lengths)[::-1]

        batch_x_i = np.array(batch_x_i)[order]
        batch_y_i = np.array(batch_y_i)[order]
        batch_cond_i = np.array(batch_cond_i)[order]

        yield batch_x_i, batch_y_i, batch_cond_i


def batchify_defs_with_examples(data, vocab, cond_vocab, batch_size):
    i = 0
    while i < len(data):
        batch_x_i = []
        batch_y_i = []
        batch_cond_i = []
        batch_context_i = []
        lengths = []
        for j in range(batch_size):
            batch_x_i.append(
                vocab.encode_seq(data[i][0] + data[i][1])
            )
            batch_y_i.append(
                vocab.encode_seq(data[i][1] + [constants.EOS_WORD])
            )
            batch_cond_i.append(
                cond_vocab.encode_seq(data[i][0])
            )
            batch_context_i.append(
                cond_vocab.encode_seq(data[i][2])
            )
            lengths.append(len(batch_x_i[-1]))

            i += 1
            if i >= len(data):
                break

        maxlen = max(lengths)
        for j in range(len(batch_x_i)):
            batch_x_i[j] = pad(batch_x_i[j], maxlen)
            batch_y_i[j] = pad(batch_y_i[j], maxlen)

        order = np.argsort(lengths)[::-1]

        batch_x_i = np.array(batch_x_i)[order]
        batch_y_i = np.array(batch_y_i)[order]
        batch_cond_i = np.array(batch_cond_i)[order]
        batch_context_i = np.array(batch_context_i)[order]
        yield batch_x_i, batch_y_i, batch_cond_i, batch_context_i


class Dictionary(object):

    def __init__(self):
        self.w2i = {
            constants.PAD_WORD: 0,
            constants.UNK_WORD: 1,
            constants.BOS_WORD: 2,
            constants.EOS_WORD: 3,
        }
        self.i2w = [
            constants.PAD_WORD,
            constants.UNK_WORD,
            constants.BOS_WORD,
            constants.EOS_WORD,
        ]

    def add_word(self, word):
        if word not in self.w2i:
            self.w2i[word] = len(self.i2w)
            self.i2w.append(word)

    def encode_seq(self, seq):
        out = []
        for word in seq:
            if word in self.w2i:
                out.append(self.w2i[word])
            else:
                out.append(1)  # constants.UNK
        return out

    def decode_seq(self, seq):
        out = []
        for idx in seq:
            if idx in self.i2w:
                out.append(self.i2w[idx])
            else:
                raise "Some shit here!"
        return out

    def save(self, path):
        with open(path, "w") as outfile:
            json.dump(self.i2w, outfile, indent=4)

    def restore(self, path):
        with open(path, "r") as infile:
            self.i2w = json.load(infile)

        self.w2i = {}
        for i, word in enumerate(self.i2w):
            self.w2i[word] = i


class WikiText(object):

    def __init__(self, train, val, test):
        self.vocab = Dictionary()
        self.train = self.tokenize(open(train, "r").read().lower().split())
        self.val = self.tokenize(open(val, "r").read().lower().split())
        self.test = self.tokenize(open(test, "r").read().lower().split())

    def tokenize(self, data):
        for i in range(len(data)):
            self.vocab.add_word(data[i])
        return data


class Definitions(object):

    def __init__(self, train, val, test, with_examples=False, vocab_path=None):
        self.with_examples = with_examples

        with open(train, "r") as infile:
            self.train = json.load(infile)
        with open(val, "r") as infile:
            self.val = json.load(infile)
        with open(test, "r") as infile:
            self.test = json.load(infile)

        self.vocab = Dictionary()
        if vocab_path is not None:
            self.vocab.restore(vocab_path)
        else:
            self.tokenize_defs(self.train)
            self.tokenize_defs(self.val)
            self.tokenize_defs(self.test)

        if self.with_examples:
            self.cond_vocab = Dictionary()
            self.tokenize_conds(self.train)
            self.tokenize_conds(self.val)
            self.tokenize_conds(self.test)
        else:
            self.cond_vocab = None

    def tokenize_defs(self, data):
        for i in range(len(data)):
            for j in range(len(data[i][1])):
                self.vocab.add_word(data[i][1][j])

    def tokenize_conds(self, data):
        for i in range(len(data)):
            self.cond_vocab.add_word(data[i][0][0])
            for j in range(len(data[i][2])):
                self.cond_vocab.add_word(data[i][2][j])
