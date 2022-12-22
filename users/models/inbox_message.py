from asgiref.sync import sync_to_async
from django.db import models

from core.models import Config
from stator.models import State, StateField, StateGraph, StatorModel


class InboxMessageStates(StateGraph):
    received = State(try_interval=300)
    processed = State(try_interval=86400, attempt_immediately=False)
    purged = State()  # This is actually deletion, it will never get here

    received.transitions_to(processed)
    processed.transitions_to(purged)

    @classmethod
    async def handle_received(cls, instance: "InboxMessage"):
        from activities.models import Post, PostInteraction
        from users.models import Follow, Identity, Report

        match instance.message_type:
            case "follow":
                await sync_to_async(Follow.handle_request_ap)(instance.message)
            case "announce":
                await sync_to_async(PostInteraction.handle_ap)(instance.message)
            case "like":
                await sync_to_async(PostInteraction.handle_ap)(instance.message)
            case "create":
                match instance.message_object_type:
                    case "note":
                        await sync_to_async(Post.handle_create_ap)(instance.message)
                    case "question":
                        pass  # Drop for now
                    case unknown:
                        if unknown in Post.Types.names:
                            await sync_to_async(Post.handle_create_ap)(instance.message)
                        else:
                            raise ValueError(
                                f"Cannot handle activity of type create.{unknown}"
                            )
            case "update":
                match instance.message_object_type:
                    case "note":
                        await sync_to_async(Post.handle_update_ap)(instance.message)
                    case "person":
                        await sync_to_async(Identity.handle_update_ap)(instance.message)
                    case "service":
                        await sync_to_async(Identity.handle_update_ap)(instance.message)
                    case "group":
                        await sync_to_async(Identity.handle_update_ap)(instance.message)
                    case "organization":
                        await sync_to_async(Identity.handle_update_ap)(instance.message)
                    case "application":
                        await sync_to_async(Identity.handle_update_ap)(instance.message)
                    case "question":
                        pass  # Drop for now
                    case unknown:
                        if unknown in Post.Types.names:
                            await sync_to_async(Post.handle_update_ap)(instance.message)
                        else:
                            raise ValueError(
                                f"Cannot handle activity of type update.{unknown}"
                            )
            case "accept":
                match instance.message_object_type:
                    case "follow":
                        await sync_to_async(Follow.handle_accept_ap)(instance.message)
                    case unknown:
                        raise ValueError(
                            f"Cannot handle activity of type accept.{unknown}"
                        )
            case "reject":
                match instance.message_object_type:
                    case "follow":
                        await sync_to_async(Follow.handle_reject_ap)(instance.message)
                    case unknown:
                        raise ValueError(
                            f"Cannot handle activity of type reject.{unknown}"
                        )
            case "undo":
                match instance.message_object_type:
                    case "follow":
                        await sync_to_async(Follow.handle_undo_ap)(instance.message)
                    case "like":
                        await sync_to_async(PostInteraction.handle_undo_ap)(
                            instance.message
                        )
                    case "announce":
                        await sync_to_async(PostInteraction.handle_undo_ap)(
                            instance.message
                        )
                    case unknown:
                        raise ValueError(
                            f"Cannot handle activity of type undo.{unknown}"
                        )
            case "delete":
                # If there is no object type, it's probably a profile
                if not isinstance(instance.message["object"], dict):
                    await sync_to_async(Identity.handle_delete_ap)(instance.message)
                else:
                    match instance.message_object_type:
                        case "tombstone":
                            await sync_to_async(Post.handle_delete_ap)(instance.message)
                        case "note":
                            await sync_to_async(Post.handle_delete_ap)(instance.message)
                        case unknown:
                            raise ValueError(
                                f"Cannot handle activity of type delete.{unknown}"
                            )
            case "add":
                # We are ignoring these right now (probably pinned items)
                pass
            case "remove":
                # We are ignoring these right now (probably pinned items)
                pass
            case "flag":
                # Received reports
                await sync_to_async(Report.handle_ap)(instance.message)
            case unknown:
                raise ValueError(f"Cannot handle activity of type {unknown}")
        return cls.processed

    @classmethod
    async def handle_processed(cls, instance: "InboxMessage"):
        if instance.state_age > Config.system.inbox_message_purge_after:
            await InboxMessage.objects.filter(pk=instance.pk).adelete()


class InboxMessage(StatorModel):
    """
    an incoming inbox message that needs processing.

    Yes, this is kind of its own message queue built on the state graph system.
    It's fine. It'll scale up to a decent point.
    """

    message = models.JSONField()

    state = StateField(InboxMessageStates)

    @property
    def message_type(self):
        return self.message["type"].lower()

    @property
    def message_object_type(self) -> str | None:
        if isinstance(self.message["object"], dict):
            return self.message["object"]["type"].lower()
        else:
            return None

    @property
    def message_type_full(self):
        if isinstance(self.message.get("object"), dict):
            return f"{self.message_type}.{self.message_object_type}"
        else:
            return f"{self.message_type}"

    @property
    def message_actor(self):
        return self.message.get("actor")
