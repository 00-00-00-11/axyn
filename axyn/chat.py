import logging
from datetime import datetime, timedelta

import emoji
import discord
from discord.ext import commands, tasks, flags
from chatterbot.conversation import Statement

from caps import capitalize


# Set up logging
logger = logging.getLogger(__name__)


class Summon:
    def __init__(self, channel, cmd, resp, debug=False):
        """
        A summoning to a channel.

        :param channel: The channel the bot has been summoned to.
        :param cmd: The user's command message.
        :param resp: The "summoned" response to the command.
        :param debug: Whether this frame is in debug mode.
        """

        self.channel = channel
        self.cmd = cmd
        self.resp = resp
        self.debug = debug

        self.last_activity = resp


COMMAND_PREFIXES = [
    '!', '?', '&', '-', '$', '£',
    'a!', 'c!', 'pm!', 'p.', 'v.', 'vc/'
]

def is_command(text):
    """Check if the given text appears to be a command."""

    for prefix in COMMAND_PREFIXES:
        # Compare against each stored prefix
        if text.startswith(prefix):
            return True

    return False


class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.summons = dict()

        self.auto_unsummon.start()

    def cog_unload(self):
        self.auto_unsummon.cancel()

    @flags.add_flag('--debug', action='store_const', const=True)
    @flags.command()
    async def summon(self, ctx, **flags):
        """Summon the bot to listen to this channel."""

        if self.summons.get(ctx.channel.id) is not None:
            # Bot was already summoned
            await ctx.send(embed=discord.Embed(
                description="I have already been summoned!",
                colour=discord.Colour.orange()
            ))
            return

        # Respond to the command
        e = discord.Embed(
            title='Summon frame opened',
            description=(
                'I am now responding to messages in this channel.\n'
                'Use `c!unsummon` when you are finished talking.'
            ),
            colour=discord.Colour.green()
        )
        e.set_footer(
            text=ctx.author.name,
            icon_url=ctx.author.avatar_url
        )
        resp = await ctx.send(embed=e)

        # Store the summoning
        self.summons[ctx.channel.id] = Summon(
            ctx.channel,
            ctx.message,
            resp,
            flags['debug']
        )

        try:
            # Delete the command message
            await ctx.message.delete()
        except discord.Forbidden:
            # It doesn't matter if we can't
            pass

    @commands.command()
    async def unsummon(self, ctx):
        """Stop listening to this channel."""

        if self.summons.get(ctx.channel.id) is None:
            # Bot was never summoned
            await ctx.send(embed=discord.Embed(
                description="I was never summoned!",
                colour=discord.Colour.orange()
            ))
            return

        # Get conversation ID
        id = self.conv_id(ctx.message)

        # Unsummon
        del self.summons[ctx.channel.id]

        # Respond to the command
        e = discord.Embed(
            title='Summon frame closed',
            description='I am no longer responding to messages in this channel.',
            colour=discord.Colour.red()
        )
        e.add_field(name='Conversation ID', value=id)
        e.set_footer(
            text=ctx.author.name,
            icon_url=ctx.author.avatar_url
        )
        resp = await ctx.send(embed=e)

        try:
            # Delete the command message
            await ctx.message.delete()
        except discord.Forbidden:
            # It doesn't matter if we can't
            pass

    @tasks.loop(minutes=1)
    async def auto_unsummon(self):
        """Automatically close inactive frames."""

        logger.debug('Checking auto unsummon...')

        for summon in list(self.summons.values()):
            # Get time of last activity in the frame
            last = summon.last_activity.created_at

            logger.debug(
                'Last activity in %i was "%s"',
                summon.channel.id,
                summon.last_activity.clean_content
            )

            # Check if it was over 2 minutes ago
            if last <= datetime.now() - timedelta(minutes=2):
                # Close the frame
                logger.info(
                    'Automatically closing summon frame %i after inactivity',
                    summon.channel.id
                )

                # Get conversation ID
                id = self.conv_id(summon.resp)

                # Unsummon
                del self.summons[summon.channel.id]

                # Send a notice to the channel
                e = discord.Embed(
                    title='Summon frame expired',
                    description=(
                        'I am no longer responding to messages in this channel.'
                        ' This frame was automatically closed after 2 minutes'
                        ' of inactivity.'
                    ),
                    colour=discord.Colour.red()
                )
                e.add_field(name='Conversation ID', value=id)
                resp = await summon.channel.send(embed=e)

    @auto_unsummon.before_loop
    async def before_auto_unsummon(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, msg):
        """
        Process a message and send a chatbot response to the channel.

        If the bot doesn't understand, no message will be sent.
        """

        logger.info('Receved message "%s"', msg.clean_content)
        if self.should_ignore(msg): return

        # Build query statement
        statement = await self.query_statement(msg)

        if self.should_respond(msg):
            # Update summon
            summon = self.summons[msg.channel.id]
            summon.last_activity = msg

            # Get a chatbot response
            async with msg.channel.typing():
                logger.info('Getting response')
                response = self.bot.chatter.get_response(statement)

            if response.confidence >= 0.5:
                # Send to Discord
                await self.send_response(response, msg, summon.debug)
            else:
                # Bot is uncertain
                logger.info('Bot did not understand')

                if summon.debug:
                    # Send a debug message
                    await self.debug_no_response(response, msg)
                else:
                    # Do not send anything
                    logger.info('No message sent')

        if (statement.in_response_to is not None) and self.should_learn(msg):
            # Learn from the statement
            self.bot.chatter.learn_response(
                statement,
                statement.in_response_to
            )

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Learn unicode emoji reactions as responses."""

        logger.info(
            'Received reaction on "%s"',
            reaction.message.clean_content
        )

        # Use original message for ignore checking
        if self.should_learn_reaction(reaction.message, user):
            # Check if this is a unicode emoji or a custom one
            if type(reaction.emoji) == str:
                # Build a Statement
                statement = Statement(
                    text=reaction.emoji,
                    in_response_to=reaction.message.clean_content,
                    conversation=self.conv_id(reaction.message),
                    persona=user.id,
                )

                # Learn the emoji as a response
                self.bot.chatter.learn_response(
                    statement,
                    statement.in_response_to
                )

    def should_ignore(self, msg):
        """Check if the given message should be completely ignored."""

        # Check if the author is a bot / system message
        if msg.author.bot or msg.type != discord.MessageType.default:
            logger.info('Author is a bot, ignoring')
            return True

        if len(msg.content) == 0:
            logger.info('Message has no text, ignoring')
            return True

        if is_command(msg.content):
            logger.info('Message appears to be a bot command, ignoring')
            return True

        return False

    def should_respond(self, msg):
        """Check if the given message should be responded to."""

        if not self.active_for(msg):
            logger.info('Channel is inactive, not sending a response')
            return False

        return True

    def should_learn(self, msg):
        """Check if the given message should be learned from."""

        if not self.active_for(msg):
            # Check channel names for bad strings
            if 'spam' in msg.channel.name:
                logger.info('Channel name contains "spam", not learning')
                return False
            if 'command' in msg.channel.name: # Will also catch "commands"
                logger.info('Channel name contains "command", not learning')
                return False
            if 'meme' in msg.channel.name:
                logger.info('Channel name contains "meme", not learning')
                return False

        return True

    def should_learn_reaction(self, msg, reaction_user):
        """Check if a reaction on the given message should be learned."""

        if reaction_user == msg.author:
            logger.info('Reaction is from message author, not learning it')
            return False

        if msg.author.bot and msg.author != self.bot.user:
            logger.info('Message is from foreign bot, not learning reaction')
            return False

        if len(msg.content) == 0:
            logger.info('Message has no text, not learning reaction')
            return False

        if is_command(msg.content):
            logger.info(
                'Message appears to be a bot command, not learning reaction')
            return False

        return True

    def active_for(self, msg):
        """Check if the given message is within an active summon frame."""

        # Find the channel's Summon object
        summon = self.summons.get(msg.channel.id)
        if summon is None:
            return False

        # Check if the message is after the summon frame opened
        return msg.created_at > summon.resp.created_at

    async def debug_no_response(self, response, msg):
        """
        Send debug information when no good response was found.

        :param response: The uncertain response returned by Axyn
        :param msg: Message we are responding to
        """

        embed = discord.Embed(
            title='No responses found',
            description='The returned response had a low confidence.'
        )
        embed.add_field(
            name='Confidence',
            value=response.confidence
        )
        await msg.channel.send(embed=embed)

        logger.info('Sent "no response found" message')

    async def send_response(self, response, msg, debug=False):
        """
        Send the response to the given channel.

        :param response: Statement object to send
        :param msg: Message we are responding to
        :param debug: If True, attach a debugging embed
        """

        logger.info('Sending response to channel')

        # Ensure response has correct capitalization
        form_text = capitalize(response.text)

        if debug:
            # Attach debug information
            e = discord.Embed()
            e.add_field(name='Confidence', value=response.confidence)
            # Send
            self.summons[msg.channel.id].last_activity = \
                await msg.channel.send(form_text, embed=e)
        else:
            # Attempt to send emojis as a reaction
            if form_text in emoji.UNICODE_EMOJI:
                try:
                    await msg.add_reaction(form_text)
                except discord.Forbidden:
                    pass # Continue to send as message
                else:
                    return # Reaction added successfully

            # Just send raw message
            self.summons[msg.channel.id].last_activity = \
                await msg.channel.send(form_text)

    def conv_id(self, msg):
        """Get a conversation ID for the given message."""

        # Find the relevant Summon object
        summon = self.summons.get(msg.channel.id)

        if summon is not None:
            # Get timestamp
            timestamp = summon.resp.created_at.timestamp()
            # Combine into full ID
            return f'{msg.channel.id}-{timestamp}'
        else:
            # The bot was not summoned, do not include timestamp
            return f'{msg.channel.id}-unsummoned'

    async def query_statement(self, msg):
        """Build a Statement from the user's message."""

        logger.info('Building query statement')

        # Get previous message
        prev = await self.get_previous(msg)

        statement = Statement(
            # Use message contents for statement text
            msg.clean_content,
            in_response_to=prev,
            # Use Discord IDs for conversation and person
            conversation=self.conv_id(msg),
            persona=msg.author.id,
        )

         # Make sure the statement has its search text saved
        statement.search_text = self.bot.chatter.storage.tagger \
            .get_bigram_pair_string(statement.text)
        # And for in_response_to
        if statement.in_response_to is not None:
            statement.search_in_response_to = self.bot.chatter.storage.tagger \
                .get_bigram_pair_string(statement.in_response_to)

        return statement

    async def get_previous(self, msg, minutes=5):
        """
        Get the previous message to store in database.

        Find a message in the same channel as the one given, which is directly
        before and occured within X minutes. Return the text of this message
        if it is found, otherwise return None.
        """

        logger.info('Looking for a previous message')

        prev = await msg.channel.history(
            # Find the message directly before this
            limit=1,
            oldest_first=False,
            before=msg,
            # Limit to messages within timeframe
            after=msg.created_at - timedelta(minutes=minutes)
        ).flatten()

        if len(prev) > 0:
            # We found a previous message
            prev_msg = prev[0]

            if prev_msg.author == msg.author:
                logger.info('Found message has same author, not returning')
                return

            if prev_msg.author.bot and prev_msg.author != self.bot.user:
                logger.info(
                    'Found message is from a bot other than '
                    'ourself, not returning'
                )
                return

            if len(prev_msg.content) == 0:
                logger.info('Found message has no text, not returning')
                return

            # This message is valid!
            logger.info('Found "%s"', prev_msg.clean_content)
            return  prev_msg.clean_content
        else:
            # We didn't find any messages
            logger.info('No message found')


def setup(bot):
    bot.add_cog(Chat(bot))