import random
from math import log
from collections import defaultdict
import warnings

from helpers.bit_board import BitBoard, bit_find_actions, bit_update_actions, bit_a_update_actions
from helpers.movements import check_adjacent_cells, is_valid
from helpers.sim_board import SimBoard, find_actions, update_actions
from referee import agent
from referee.game.actions import Action
from referee.game.board import CellState
from referee.game.constants import MAX_TURNS
from referee.game.coord import Coord
from referee.game.player import PlayerColor
from timeit import default_timer as timer


CLOSE_TO_END = 100

class MCTSNode:
    """
    Node class for the Monte Carlo Tree Search algorithm
    """

    def __init__(
        self,
        board: BitBoard,
        parent: "MCTSNode | None" = None,
        parent_action: Action | None = None,
    ):
        """
        Initialize the node with the current board state
        """
        self.board: BitBoard = board
        self.parent: MCTSNode | None = parent
        self.parent_action: Action | None = parent_action

        self.my_actions: list[Action]

        # parent.parent: parent with same color
        if parent and parent.parent and parent.parent.my_actions:
            self.my_actions = parent.parent.my_actions.copy()
            self.my_actions = update_actions(
                parent.parent.board.state,
                self.board.state,
                self.my_actions,
                board.turn_color,
            )
            # if (len(self.my_actions) == 0):
            #     print("ERROR: bit_update_actions returned no actions")
            #print(f"bit_update_actions found {len(self.my_actions)} actions")
        else:
            # print("no parent")
            self.my_actions = bit_find_actions(board, board.turn_color)
            if (len(self.my_actions) == 0):
                print("ERROR: bit_find_actions returned no actions")
            #print(f"bit_find_actions found {len(self.my_actions)} actions")

        self.untried_actions = self.my_actions.copy()  # actions not yet tried
        
        #print(f"{len(self.untried_actions)} moves found for mcts")
        
        self.__action_to_children: dict[Action, "MCTSNode"] = (
            {}
        )  # my actions to child node

        self.color: PlayerColor = board.turn_color
        self.num_visits = 0

        self.results = defaultdict(int)
        self.results[1] = 0  # win
        # self.results[-1] = 0  # loss

        self.danger = False
        self.winning_color: PlayerColor | None = None
        self.estimated_time: float = 0

    def expand(self, action: Action | None = None):
        """
        Expand the current node by adding a new child node
        Using opponent move as action
        """
        board_node: BitBoard = self.board.copy()
        if action is None:
            if self.untried_actions:
                action = random.choice(self.untried_actions)
            else:
                return random.choice(list(self.__action_to_children.values()))

        board_node.apply_action(action)

        child_node: MCTSNode = MCTSNode(
            board_node,
            parent=self,
            parent_action=action,
        )
        child_node.estimated_time = self.estimated_time
        if action in self.untried_actions:
            self.untried_actions.remove(action)
        self.__action_to_children[action] = child_node
        return child_node

    def is_terminal_node(self):
        return self.board.game_over

    def is_fully_expanded(self):
        if not self.untried_actions:
            return True
        return False

    def rollout_turns(self, times: int) -> int:
        """
        Simulate a random v random game from the current node
        """
        print("rolling out for turns")
        push_steps = []
        current_node = self
        tried_times = 0
        while (tried_times != times):
            this_push_step = 0
            while not current_node.is_terminal_node():
                # light playout policy
                current_node = current_node.tree_policy()
                this_push_step += 1
                # print("pushing step: ", push_step)
                if not current_node:
                    warnings.warn("ERROR: No tree policy node found in rollout")
                    return self.board.turn_count
                current_node.backpropagate(current_node.board.winner_color)
            tried_times += 1
            push_steps.append(this_push_step)
        _sum = 0
        for i in push_steps:
            _sum += i
        avg = _sum / tried_times
        result = round(avg / 2) # half of the turns are ours
        return result
    
    def new_rollout(self, max_steps) -> 'MCTSNode | None':
        """
        Simulate a random v random game from the current node
        not pushing all the way to the end of the game but stopping at max_steps
        """
        # make sure max_steps is even so that both players get equal number of moves
        if max_steps / 2 == 1:
            max_steps += 1
        push_step = 0
        current_node = self
        while not current_node.is_terminal_node() and push_step < max_steps:
            # light playout policy
            current_node = current_node.tree_policy()
            push_step += 1
            # print("pushing step: ", push_step)
            if not current_node:
                warnings.warn("ERROR: No tree policy node found in rollout")
                return None
        if current_node.is_terminal_node():
            current_node.danger = True
            current_node.winning_color = current_node.board.winner_color
            return current_node
        if current_node.greedy_judge(self.color) > self.greedy_judge(self.color):
            current_node.winning_color = self.color
        else:
            current_node.winning_color = self.color.opponent
        return current_node

    def backpropagate(self, result: PlayerColor | None):
        """
        Backpropagate the result of the simulation up the tree
        """
        self.num_visits += 1
        if result == self.color:
            self.results[1] += 1
        # elif result == root_color.opponent:
        #     self.results[-1] += 1
        if self.parent:
            self.parent.danger = self.danger
            self.parent.backpropagate(result)

    def best_child(self, c_param=1.4) -> "MCTSNode":
        """
        Select the best child node based on the UCB1 formula
        """
        best_score: float = float("-inf")
        best_child = None
        for child in self.__action_to_children.values():
            if child.num_visits <= 0 or self.num_visits <= 0:
                exploit: float = child.results[1]
                explore: float = 0.0
            else:
                exploit: float = child.results[1] / child.num_visits
                explore: float = (
                    c_param * (log(self.num_visits) / child.num_visits) ** 0.5
                )

            score: float = exploit + explore
            if score > best_score:
                best_score = score
                best_child = child
        if not best_child:
            print("ERROR: No best child found")
            print(len(self.__action_to_children))
            exit()
        return best_child

    def tree_policy(self) -> "MCTSNode | None":
        """
        Select a node to expand based on the tree policy
        """
        # select nodes to expand
        if not self.is_terminal_node():
            if not self.is_fully_expanded():
                return self.expand()
            else:
                if not self.my_actions:
                    print("ERROR: No actions available")
                    return None
                if self.__action_to_children:
                    print("finish expanding, looking for best child")
                    return self.best_child()
        return self

    def best_action(self, steps=MAX_TURNS, sim_no=100) -> Action | None:
        """
        Perform MCTS search for the best action
        """
        sim_count = 0
        start_time = timer()
        for _ in range(sim_no):
            if timer() - start_time > self.estimated_time:
                break
            # expansion
            v: MCTSNode | None = self.tree_policy()
            if not v:
                print("ERROR: No tree policy node found")
                return None
            # simulation
            if v.is_terminal_node() and v.board.winner_color == self.color:
                return v.parent_action
            # rollout with heuristic and max_steps
            end_node = v.new_rollout(steps)
            if end_node:
                end_node.backpropagate(end_node.winning_color)
            sim_count += 1
        print("sim_count: ", sim_count)
        
        # time per simulation average
        print("average time per simulation: ", (timer() - start_time) / sim_count)
        
        # return best action
        best_child = self.best_child(c_param=0.0)
        if best_child:
            print("best action: ", best_child.parent_action)
            return best_child.parent_action

        # if no best child, print error + return None
        print("ERROR: No best child found")
        return None

    def get_random_move(self) -> Action | None:
        """
        Get a random move for the current state
        """
        return random.choice(list(self.my_actions))
    
    def greedy_judge(self, agent_color: PlayerColor|None = None) -> int:
        """
        heuristic function to predict if this player is winning
        """ 
        if agent_color == None:
            agent_color = self.color
        result = 0
        if self.color == agent_color:
            result += len(self.my_actions)
        else:
            result -= len(self.my_actions)
        if self.board.turn_count > CLOSE_TO_END:
            if agent_color == PlayerColor.RED:
                result += round((self.board._red_state - self.board._blue_state) + 
                                len(self.my_actions)/ MAX_TURNS - self.board.turn_count)
            else:
                result += round((self.board._blue_state - self.board._red_state + 
                                 len(self.my_actions)) / MAX_TURNS - self.board.turn_count)
        return result
    
    def greedy_explore(self) -> Action | None:
        """
        Pick the action with the highest heuristic value
        """
        start_time = timer()
        best_action : Action| None = None
        best_value = float("-inf")
        while self.untried_actions:
            if timer() - start_time > self.estimated_time:
                break
            action = random.choice(self.untried_actions)
            # new_node is opponent's turn
            new_node = self.get_child(action)
            value = new_node.greedy_judge(self.color)
            if value > best_value:
                best_value = value
                best_action = action
                
        return best_action
    
    def chop_nodes_except(self, node: "MCTSNode | None" = None):
        """
        To free up memory, delele all useless nodes
        need to call gc.collect() after this function
        params: node: node to keep as it will be the new root
        """
        if node:
            # main branch
            for child in self.__action_to_children.values():
                # child node to keep, all children of this node will be saved
                if node and child == node:
                    continue
                else:
                    # recursively delete all other children
                    child.chop_nodes_except()
        else:
            for child in self.__action_to_children.values():
                child.chop_nodes_except()
            del self.__action_to_children
            del self.board
            del self.parent
            del self.parent_action
            del self.my_actions
            del self.results
            del self.color
            del self.num_visits
            del self.untried_actions
            del self.danger

    def get_child(self, action: Action):
        """
        Function to wrap the action_to_children dictionary in case of KeyError
        """
        if action in self.__action_to_children:
            return self.__action_to_children[action]
        else:
            # did not expand it
            self.expand(action)
            return self.__action_to_children[action]
