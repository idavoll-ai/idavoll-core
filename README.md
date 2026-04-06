# Adavoll-core

Adavoll Core is a highly customizable core framework for AI agents, focusing on personality shaping, expressive style control, and dynamic context management.

Todo：
1. Long-term memory: 
   Maybe we can first split from {name}.yaml and store it in data/memory/{name}.json.
2. Self-Growth Mechanisms: 
   How to Assess Growth Trajectories and Configure Varying Levels of Growth Authority
3. Wizard Refacting:
   It cannot serve as a component subordinate to the agent; perhaps it should exist in the form of a plugin or tool.
4. Currently, one session corresponds to one topic. However, in real business scenarios, a session should belong to a specific agent, not a specific group/chat room.
5. Based on point 4, a concurrency issue arises regarding message generation.I should center the design around topics, with the topic acting as the core collection.A possible approach is similar to subagents: having a main Session along with subSessions. Each agent creates its own session when joining. In this case, could a sandbox be implemented to support this?If the number of sessions becomes too large, a review mechanism should be triggered.
6. checkpoint [x]
7. How to design a more efficient review system[IMPORTANT]