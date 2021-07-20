#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from typing import Optional
from parlai.core.params import ParlaiParser
from parlai.core.opt import Opt
import os
from typing import Any, List

import numpy as np

from parlai.utils.io import PathManager
from parlai.core.message import Message
from parlai.core.teachers import FixedDialogTeacher
from .build import build


DEFAULT_TRAIN_EXPERIENCER_ONLY = False
DEFAULT_REMOVE_POLITICAL_CONVOS = False
PERSPECTIVES = ('experiencer', 'responder', 'both')
DEFAULT_PERSPECTIVE = 'train:both,test:experiencer,valid:experiencer'


class EmpatheticDialoguesTeacher(FixedDialogTeacher):
    def __init__(self, opt, shared=None):
        super().__init__(opt, shared)
        self.opt = opt
        base_datatype = self.datatype.split(':')[0]
        self.datapath = os.path.join(
            self.opt['datapath'],
            'empatheticdialogues',
            'empatheticdialogues',
            base_datatype + '.csv',
        )
        # ignore the perspective argument if this train_experiencer_only arg is True
        experiencer_side_only = (
            opt.get('train_experiencer_only', DEFAULT_TRAIN_EXPERIENCER_ONLY)
            and base_datatype == 'train'
        )
        if experiencer_side_only:
            self.perspective = 'experiencer'
        else:
            try:
                perspective_arg = opt.get('perspective', DEFAULT_PERSPECTIVE)
                if perspective_arg in PERSPECTIVES:
                    self.perspective = perspective_arg
                else:
                    perspective_arg = dict(
                        map(
                            lambda s: map(str.strip, s.split(':')),
                            perspective_arg.split(','),
                        )
                    )
                    self.perspective = perspective_arg[base_datatype]
                    if self.perspective not in PERSPECTIVES:
                        raise Exception
            except Exception:
                print(
                    f'Error: Invalid perspective argument string. --help for more info.'
                )
        if not shared:
            print(
                f'[EmpatheticDialoguesTeacher] Perspectives: {self.perspective}, for datatype: {base_datatype}'
            )
        self.remove_political_convos = opt.get(
            'remove_political_convos', DEFAULT_REMOVE_POLITICAL_CONVOS
        )

        if shared:
            self.data = shared['data']
        else:
            build(opt)
            self._setup_data(base_datatype)

        self.num_exs = sum([len(d) for d in self.data])
        self.num_eps = len(self.data)
        self.reset()

    @classmethod
    def add_cmdline_args(
        cls, parser: ParlaiParser, partial_opt: Optional[Opt] = None
    ) -> ParlaiParser:
        super().add_cmdline_args(parser, partial_opt)
        agent = parser.add_argument_group('EmpatheticDialogues teacher arguments')
        agent.add_argument(
            '--train-experiencer-only',
            type='bool',
            default=DEFAULT_TRAIN_EXPERIENCER_ONLY,
            # i.e. do not include the other side of the conversation where the Listener
            # (responder) utterance would be the text and the Speaker (experiencer)
            # utterance would be the label
            help='In the train set, only use Speaker (experiencer) utterances as text and Listener (responder) utterances as labels.',
        )
        # NOTE: the perspective argument is overriden by the train_experiencer_only arg
        agent.add_argument(
            '--perspective',
            type=str,
            default=DEFAULT_PERSPECTIVE,
            # i.e. if 'responder', do not include the other side of the conversation where the Speaker
            # (experiencer) utterance would be the text and the Listener (responder)
            # utterance would be the label
            help='Specify what perspective ("experiencer" or "responder" or "both") is included. For more detail, specify the set as well like so: "train:experiencer,test:both,valid:both". NOTE: this is overriden by the train_experiencer_only arg.',
        )
        agent.add_argument(
            '--remove-political-convos',
            type='bool',
            default=DEFAULT_REMOVE_POLITICAL_CONVOS,
            help='Remove all conversations containing an utterance marked as political',
        )
        return parser

    def num_episodes(self):
        return self.num_eps

    def num_examples(self):
        return self.num_exs

    def _setup_data(self, base_datatype):

        if self.opt.get('deepmoji') is not None:
            self.embed = np.load(self.opt['deepmoji'] + base_datatype + ".npy")

        if self.opt.get('fasttextloc') is not None and self.opt.get('prepend', -1) > 0:
            try:
                import fastText
            except ImportError:
                raise ImportError("Please run 'pip install fasttext'.")
            ftpath = self.opt['fasttextloc']
            ftmodel = fastText.FastText.load_model(ftpath)

        with PathManager.open(self.datapath) as f:
            df = f.readlines()

        turn_idx = 1
        responder_text_dialogue = []
        experiencer_text_dialogue = []
        self.data = []
        for i in range(1, len(df)):

            cparts = df[i - 1].strip().split(",")
            sparts = df[i].strip().split(",")

            if cparts[0] == sparts[0]:

                # Check that the turn number has incremented correctly
                turn_idx += 1
                assert (
                    int(cparts[1]) + 1 == int(sparts[1]) and int(sparts[1]) == turn_idx
                )

                contextt = cparts[5].replace("_comma_", ",")
                label = sparts[5].replace("_comma_", ",")
                prompt = sparts[2]
                sit = sparts[3].replace("_comma_", ",")
                if len(sparts) == 9:
                    if sparts[8] != '':
                        inline_label_candidates = [
                            cand.replace("_comma_", ",").replace("_pipe_", "|")
                            for cand in sparts[8].split('|')
                        ]
                    else:
                        inline_label_candidates = []
                elif len(sparts) == 8:
                    inline_label_candidates = []
                else:
                    raise ValueError(f'Line {i:d} has the wrong number of fields!')

                context_emb, cand_emb = None, None
                if self.opt.get('deepmoji') is not None:
                    context_emb = self.embed[i - 2]
                    cand_emb = self.embed[i - 1]

                ft_ctx, ft_cand = None, None
                if (
                    self.opt.get('fasttextloc') is not None
                    and self.opt.get('prepend', -1) > 0
                ):
                    ft_ctx = ""
                    gettop, _ = ftmodel.predict(contextt, k=self.opt['prepend'])
                    for f in gettop:
                        ft_ctx = f.split("_")[-1] + " " + ft_ctx
                    ft_cand = ""
                    gettop, _ = ftmodel.predict(label, k=self.opt['prepend'])
                    for f in gettop:
                        ft_cand = f.split("_")[-1] + " " + ft_cand

                # Check if either the text or label are marked as being political
                is_political = '<POLITICAL>' in cparts[7] or '<POLITICAL>' in sparts[7]

                dialogue_parts = [
                    contextt,
                    label,
                    prompt,
                    sit,
                    context_emb,
                    cand_emb,
                    ft_ctx,
                    ft_cand,
                    inline_label_candidates,
                    is_political,
                ]

                if int(sparts[1]) % 2 == 0:
                    # experiencer is the "text" and responder is the "label"
                    experiencer_text_dialogue.append(dialogue_parts)
                else:
                    # responder is the "text" and experiencer is the "label"
                    responder_text_dialogue.append(dialogue_parts)

            else:

                # We've finished the previous episode, so add it to the data
                turn_idx = 1
                self.data += self._select_dialogues_to_add(
                    experiencer_text_dialogue, responder_text_dialogue
                )
                experiencer_text_dialogue = []
                responder_text_dialogue = []

        # Add in the final episode
        self.data += self._select_dialogues_to_add(
            experiencer_text_dialogue, responder_text_dialogue
        )

    def _select_dialogues_to_add(
        self,
        experiencer_text_dialogue: List[List[Any]],
        responder_text_dialogue: List[List[Any]],
    ) -> List[List[List[Any]]]:
        """
        Return conversation halves to add to self.data.

        Given lists corresponding to the conversation turns from both sides of the
        conversation, return only the list(s) that will be added to self.data.
        Optionally filter by side of the conversation or by whether the conversation
        contains any political language.
        """
        if self.remove_political_convos and any(
            [turn[9] for turn in experiencer_text_dialogue + responder_text_dialogue]
        ):
            return []
        else:
            selected_dialogues = []
            if len(experiencer_text_dialogue) > 0 and self.perspective != 'responder':
                selected_dialogues.append(experiencer_text_dialogue)
            if len(responder_text_dialogue) > 0 and self.perspective != 'experiencer':
                selected_dialogues.append(responder_text_dialogue)
            return selected_dialogues

    def get(self, episode_idx, entry_idx=0):
        ep = self.data[episode_idx]
        ep_i = ep[entry_idx]
        episode_done = entry_idx >= (len(ep) - 1)
        action = Message(
            {
                'situation': ep_i[3],
                'emotion': ep_i[2],
                'text': ep_i[0],
                'labels': [ep_i[1]],
                'prepend_ctx': ep_i[6],
                'prepend_cand': ep_i[7],
                'deepmoji_ctx': ep_i[4],
                'deepmoji_cand': ep_i[5],
                'episode_done': episode_done,
                'label_candidates': ep_i[8],
            }
        )

        return action

    def share(self):
        shared = super().share()
        shared['data'] = self.data
        return shared


class ExperiencerEmpatheticDialoguesTeacher(EmpatheticDialoguesTeacher):
    """
    Class for generating the experiencer utterances based on a prompt/emotions.
    """

    def __init__(self, opt, shared=None):
        opt['perspective'] = 'responder'
        super().__init__(opt, shared)
        self.include_emotion = opt['include_emotion']

    @classmethod
    def add_cmdline_args(
        cls, parser: ParlaiParser, partial_opt: Optional[Opt] = None
    ) -> ParlaiParser:
        super().add_cmdline_args(parser, partial_opt)
        agent = parser.add_argument_group(
            'ExperiencerEmpatheticDialogues teacher arguments'
        )
        agent.add_argument(
            '--include-emotion',
            type='bool',
            default=False,
            # i.e add the emotion to the text
            help='Add the emotion assigned to the text.',
        )

    def num_episodes(self):
        return len(self.data)

    def num_examples(self):
        return len(self.data)

    def get(self, episode_idx, entry_idx=0):
        ep = self.data[episode_idx]
        ep_i = ep[entry_idx]
        episode_done = entry_idx >= (len(ep) - 1)
        text = ep_i[0]
        if entry_idx == 0:
            text = ep_i[3] + '\n' + text
            if self.include_emotion:
                text = text + '\nEmotion: ' + ep_i[2]
        action = Message(
            {'text': text, 'labels': [ep_i[1]], 'episode_done': episode_done}
        )
        return action


class DefaultTeacher(EmpatheticDialoguesTeacher):
    pass
