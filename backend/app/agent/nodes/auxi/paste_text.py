from maa.agent.agent_server import AgentServer

from backend.app.shared.backend.paste_text_action import PasteChatText
from backend.app.shared.backend.paste_image_action import PasteChatImage


AgentServer.custom_action("PasteChatText")(PasteChatText)
AgentServer.custom_action("PasteChatImage")(PasteChatImage)
