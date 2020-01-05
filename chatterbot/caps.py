# Originally from my Bolt bot, with modifications
# https://github.com/AlphaMycelium/discord-bolt/blob/1d64f7e19385bc0a13ac88c027db16a5a1f26c9b/bolt/autocaps.py#L7

import nltk

# Load sentence tokenizer
nltk.download('punkt')
sent_tokenizer = nltk.data.load('tokenizers/punkt/english.pickle')


def capitalize(text):
    """Reformat a piece of text to capitalize each sentence.

    Uses NLTK to ensure that capital letters are not added where they are not
    needed. Will not modify any current capitalization.
    """

    # Split sentences into a list
    sentences = sent_tokenizer.tokenize(text)

    for i, sentence in enumerate(sentences):

        # Ignore URLs
        if sentence.startswith('http'):
            continue

        # Capitalize the sentence
        sentence = sentence[0].upper() + sentence[1:]
        sentences[i] = sentence

    # Stitch back together and return
    return ' '.join(sentences)
