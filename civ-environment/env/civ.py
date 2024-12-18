import numpy as np
import pettingzoo as pz
import gymnasium as gym
from gymnasium import spaces
from pettingzoo.utils import agent_selector
from pettingzoo.utils.env import AECEnv
import pygame
from pygame.locals import QUIT
import math

# TODO: Make sure invalid actions are handled gracefully
# Example action: 
# action = {
#    "action_type": self.MOVE_UNIT,
#    "unit_id": 0,
#    "direction": 1,  # 0: up, 1: right, 2: down, 3: left
#    "city_id": 0,    # Ignored for MOVE_UNIT
#    "project_id": 0  # Ignored for MOVE_UNIT
#}
# Might change!

class Civilization(AECEnv): 
    metadata = {'render.modes': ['human'], 'name': 'Civilization_v0'}
    def __init__(self, map_size, num_agents, max_cities=10, max_projects = 5, max_units_per_agent = 50, visibility_range=1, *args, **kwargs):
        """
        Initialize the Civilization game.
        Args:
            map_size (tuple): The size of the map (width, height).
            num_agents (int): The number of players in the game.
            max_cities (int): Maximum number of cities per agent.
            visibility_range (int): The range of visibility for each unit (the tiles the units are on, and tiles within borders are already included).
            *args: Additional positional arguments for the parent class.
            **kwargs: Additional keyword arguments for the parent class.
        """
        """
        Each agent can have cities, warrios and settlers. For a maximum of 6 agents, that's 18 slots occupied. 
        Each agent can also see resources, materials, and water. That's 3 more. 
        """

        super().__init__()
        if num_agents > 6:
            raise ValueError(
                f"Number of players ({num_agents}) exceeds the maximum allowed (6)."
            )
        self.agents = ["player_" + str(i) for i in range(num_agents)]
        self.possible_agents = self.agents[:]
        self.agent_selector = agent_selector(self.agents)
        self.current_agent = self.agent_selector.reset()
        self.num_of_agents = num_agents
        self.max_units_per_agent = max_units_per_agent
        self.max_projects = max_projects


        self.map_size = map_size
        self.map_height, self.map_width = map_size

        self.max_cities = max_cities
        # Initialize the observation spaces for each player
        self.visibility_maps = {agent: np.zeros((self.map_height, self.map_width), dtype=bool) for agent in self.agents}
        self.observation_spaces = {
            agent: spaces.Dict({
                "map": spaces.Box(
                    low=0, 
                    high=1, 
                    shape=(
                        self.map_height, 
                        self.map_width, 
                        self._calculate_num_channels()
                    ), 
                    dtype=np.float32
                ),
                "units": spaces.Box(
                    low=0,
                    high=np.inf,
                    shape=(self.max_units_per_agent, self._calculate_unit_attributes()),
                    dtype=np.float32
                ),
                "cities": spaces.Box(
                    low=0,
                    high=np.inf,
                    shape=(
                        self.max_cities,
                        self._calculate_city_attributes()
                    ),
                    dtype=np.float32
                )
            })
            for agent in self.agents
        }
        # Hold the units for each player
        self.units = {agent: [] for agent in self.agents}
        # Hold the cities for each player
        self.cities = {agent: [] for agent in self.agents}

        self.last_attacker = None
        self.last_target_destroyed = False

        # Action constants: 
        self.MOVE_UNIT = 0
        self.ATTACK_UNIT = 1
        self.FOUND_CITY = 2
        self.ASSIGN_PROJECT = 3
        self.NO_OP = 4

        # unit types

        self.UNIT_TYPE_MAPPING = { 'warrior': 0, 'settler' : 1 }
        # Initialize the action spaces for each player
        self.action_spaces = {
            agent: spaces.Dict({
                "action_type": spaces.Discrete(5),  # 0: MOVE_UNIT, 1: ATTACK_UNIT, 2: FOUND_CITY, 3: ASSIGN_PROJECT, 4: NO_OP
                "unit_id": spaces.Discrete(self.max_units_per_agent),  # For MOVE_UNIT, ATTACK_UNIT, FOUND_CITY
                "direction": spaces.Discrete(4),    # For MOVE_UNIT, ATTACK_UNIT
                "city_id": spaces.Discrete(self.max_cities),           # For ASSIGN_PROJECT
                "project_id": spaces.Discrete(self.max_projects)       # For ASSIGN_PROJECT
            })
            for agent in self.agents
        }
       
        self.visibility_range = visibility_range
        self._initialize_map()

        # Initialize Pygame:
        pygame.init()
        self.cell_size = 40  # Size of each tile in pixels
        self.window_width = self.map_width * self.cell_size
        self.window_height = self.map_height * self.cell_size
        self.screen = pygame.display.set_mode((self.window_width, self.window_height))
        pygame.display.set_caption('Civilization Environment')
        self.clock = pygame.time.Clock()
        #This can definitebly be improved, but for now it's just a placeholder.
        #This is straught from the internet, needs to be changed
    
    def observe(self, agent):
        full_map = self.map.copy()
        visibility_map = self.visibility_maps[agent]
        
        # Mask unexplored areas
        masked_map = np.where(
            visibility_map[:, :, np.newaxis], # idk if this is the correct way of doing this...
            full_map,
            np.zeros_like(full_map)  # Fill unexplored areas with zeros. Maybe change this to a different value? 
        )
        
        units_obs = np.zeros((self.max_units_per_agent, self._calculate_unit_attributes()), dtype=np.float32)
        for idx, unit in enumerate(self.units[agent]):
            if idx < self.max_units_per_agent:
                units_obs[idx] = [unit.x, unit.y, unit.health, self.UNIT_TYPE_MAPPING[unit.type]]
        
        cities_obs = self._get_agent_cities(agent)

        # Return the observation dictionary
        observation = {
            "map": masked_map,
            "units": units_obs,
            "cities": cities_obs
        }
        return observation

    def step(self, action):
        agent = self.current_agent
        action_type = action['action_type']
        
        if action_type == self.MOVE_UNIT:
            unit_id = action['unit_id']
            direction = action['direction']
            self._handle_move_unit(agent, unit_id, direction)
        
        elif action_type == self.ATTACK_UNIT:
            unit_id = action['unit_id']
            direction = action['direction']
            self._handle_attack_unit(agent, unit_id, direction)
        
        elif action_type == self.FOUND_CITY:
            unit_id = action['unit_id']
            self._handle_found_city(agent, unit_id)
        
        elif action_type == self.ASSIGN_PROJECT:
            city_id = action['city_id']
            project_id = action['project_id']
            self._handle_assign_project(agent, city_id, project_id)
        
        elif action_type == self.NO_OP:
            pass  # Do nothing
        # TODO: Implement returning the observation, termination, and reward etc. etc. bullshit
        
        # Initialize rewards
        rewards = {agent: 0 for agent in self.agents}
        # TODO: ASSIGN REWARDS, USING A REWARD FUNCTION

        # Check for termination (e.g., only one player remains)
        active_agents = [agent for agent in self.agents if self.units[agent] or self.cities[agent]] # We only leave agents that have either a city or a unit
        done = len(active_agents) <= 1

        # Prepare observations
        observations = {agent: self.observe(agent) for agent in self.agents}
        # TODO: RETURN OBSERVATIONS USING SELF.OBSERVE

        # Prepare dones
        dones = {agent: done for agent in self.agents}
        dones['__all__'] = done

        # Prepare infos
        infos = {agent: {} for agent in self.agents}

        # Advance to the next agent
        self.current_agent = self.agent_selector.next()

    
    def _handle_move_unit(self, agent, action):
        unit_id = action['unit_id']
        direction = action['direction']
        unit = self.units[agent][unit_id]
        unit.move(direction)
        self._update_visibility(agent, unit.x, unit.y)
    
    def _handle_attack_unit(self, agent, action):
        unit_id = action['unit_id']
        direction = action['direction']
        unit = self.units[agent][unit_id]
        unit.attack(direction)
        # TODO: UPDATE THE HEALTH OF THE UNIT ATTACKED
        # Done within the Unit class now
    

    def _handle_found_city(self, agent, action):
        unit_id = action['unit_id']
        unit = self.units[agent][unit_id]
        if unit.type == 'settler':
            if unit.found_city():
                new_city = self.City(unit.x, unit.y, agent, env=self)
                self.cities[agent].append(new_city)
                # Remove the settler from the game
                self.units[agent].remove(unit)
                # Update the map, visibility, and any other game state
                self._update_map_with_new_city(agent, new_city)
            else:
                # Handle invalid action
                pass

    def _handle_assign_project(self, agent, action):
        city_id = action['city_id']
        project_id = action['project_id']
        city = self.cities[agent][city_id]
        city.current_project = project_id
        city.project_duration = self._get_project_duration(project_id)

    class Unit:
        def __init__(self, x, y, unit_type, owner, env):
            self.x = x
            self.y = y
            self.type = unit_type
            self.health = 100
            self.owner = owner
            self.env = env

        def move(self, direction):
            '''
            Move in the specified directino. 

            Args: 
                direction (int): Direction to move (0: up, 1: right, 2: down, 3: left).
            '''
            new_pos = self._calculate_new_position(self.x, self.y, direction)
            if new_pos is not None:
                new_x, new_y = new_pos
                self.env._update_unit_position_on_map(self, new_x, new_y)
                self.x = new_x
                self.y = new_y
            else: 
                # Handle invalid move
                pass

        def attack(self, direction):
            '''
            Attack an enemy unit or city in the specified direction.

            Args:
                direction (int): Direction to attack (0: up, 1: right, 2: down, 3: left).
            '''
            if self.type != 'warrior':
                print(f"Unit {self} is not a warrior and cannot attack.")
                return

            target_agent, target = self._check_enemy_units_and_cities(self.x, self.y, direction, self.owner)

            if target is not None:
                print(f"{self.owner}'s warrior at ({self.x}, {self.y}) attacks {target_agent}'s {target.type} at ({target.x}, {target.y}).")
                # Inflict damage
                target.health -= 35
                print(f"Target's health is now {target.health}.")
                self.env.last_attacker = self.owner
                self.env.last_target_destroyed = False

                # Check if the target is destroyed
                if target.health <= 0:
                    print(f"Target {target.type} at ({target.x}, {target.y}) has been destroyed.")
                    self.env.last_target_destroyed = True
                    self.env._remove_unit_or_city(target)
            else:
                print(f"No enemy to attack in direction {direction} from ({self.x}, {self.y}).")
        
        # TODO: Maybe add defending? 
        
        def _calculate_new_position(self, x, y, direction):
            """
            Calculate the new position based on the direction and check if the tile is empty.
            Args:
                x (int): Current x-coordinate.
                y (int): Current y-coordinate.
                direction (int): Direction to move (0: up, 1: right, 2: down, 3: left).
            Returns:
                tuple or None: (new_x, new_y) if the move is valid; None if the move is invalid.
            """
            delta_x, delta_y = 0, 0
            if direction == 0:  # up
                delta_y = -1
            elif direction == 1:  # right
                delta_x = 1
            elif direction == 2:  # down
                delta_y = 1
            elif direction == 3:  # left
                delta_x = -1
            else:
                # direction must be [0,3] 
                return None

            new_x = x + delta_x
            new_y = y + delta_y

            # Check if new position is within map boundaries
            if not (0 <= new_x < self.env.map_width and 0 <= new_y < self.env.map_height):
                return None  

            # Check if the tile is empty of units and cities
            if self._is_tile_empty_of_units_and_cities(new_x, new_y):
                return new_x, new_y
            else:
                return None  # Tile is occupied; cannot move there
        
        def _is_tile_empty_of_units_and_cities(self, x, y):
            """
            Check if the tile at (x, y) is empty of units and cities.

            Args:
                x (int): x-coordinate.
                y (int): y-coordinate.
            Returns:
                bool: True if the tile is empty of units and cities; False otherwise.
            """
            # Check all unit channels for all agents
            for agent_idx in range(self.env.num_of_agents):
                unit_base_idx = self.env.num_of_agents + (3 * agent_idx)
                # Channels for 'city', 'warrior', 'settler'
                unit_channels = [unit_base_idx + i for i in range(3)]
                if np.any(self.env.map[y, x, unit_channels] > 0):
                    return False  # Tile has a unit or city
            return True 
        
        def _check_enemy_units_and_cities(self, x, y, direction, agent): 
            """
            Check if there are warriors or cities in the direction of the move. 
            Args:
                x (int): Current x coordinate.
                y (int): Current y coordinate.
                direction (int): Direction to move (0: up, 1: right, 2: down, 3: left).
                agent (str): The agent doing the check (so it doesn't attack its own).
            Returns:
                The target unit's owner and itself.
            """
            delta_x, delta_y = 0, 0
            if direction == 0:
                delta_y = -1
            elif direction == 1:
                delta_x = 1
            elif direction == 2:
                delta_y = 1
            elif direction == 3:
                delta_x = -1
            else:
                return None, None

            new_x = x + delta_x
            new_y = y + delta_y

             # Check map boundaries
            if not (0 <= new_x < self.map_width and 0 <= new_y < self.map_height):
                return None, None

            target = self.env._get_target_at(new_x, new_y)

            if target and target.owner != agent:
                return target.owner, target

            return None, None

        def found_city(self):
            '''
            Found a city at the current location.
            Returns:
                bool: True if the city can be founded (unit is a settler, tile is empty); False otherwise.
            '''
            # Only settlers can found cities
            if self.type == 'settler':
                return True
            return None
    
    def _update_unit_position_on_map(self, unit, new_x, new_y):
        """
        Update the map to reflect the unit's movement.

        Args:
            unit (Unit): The unit that is moving.
            new_x (int): The new x-coordinate.
            new_y (int): The new y-coordinate.
        """
        # Determine the unit's channel
        agent_idx = self.agents.index(unit.owner)
        unit_types = {'warrior': 1, 'settler': 2} # Cities can't move
        channel_offset = unit_types.get(unit.type)
        unit_channel = self.num_of_agents + (3 * agent_idx) + channel_offset

        # Clear the old position
        self.map[unit.y, unit.x, unit_channel] = 0

        # Set the new position
        self.map[new_y, new_x, unit_channel] = 1

    def _calculate_unit_attributes(self):
        return 4 # Health, X location, Y location, Type
    
    def _get_target_at(self, x, y):
        """
        Locate a unit or city at the specified coordinates.

        Args:
            x (int): x coordinate.
            y (int): y coordinate.

        Returns:
            Unit or City object if found, or None.
        """
        # Check all agents' units
        for agent in self.agents:
            for unit in self.units[agent]:
                if unit.x == x and unit.y == y:
                    return unit
            for city in self.cities[agent]:
                if city.x == x and city.y == y:
                    return city
        return None

    def _remove_unit_or_city(self, target):
        """
        Remove a unit or city from the environment.

        Args:
            target (Unit or City): The target to be removed.
        """
        owner = target.owner
        if isinstance(target, self.Unit):
            self.units[owner].remove(target)
            # Determine the channel based on unit type
            unit_types = {'warrior': 1, 'settler': 2} # To stay consistent with the representations defined before, where 0 = city
            channel_offset = unit_types.get(target.type, None)
            if channel_offset is not None:
                channel = self.num_of_agents + (3 * self.agents.index(owner)) + channel_offset
                self.map[target.y, target.x, channel] = 0
        elif isinstance(target, self.City):
            self.cities[owner].remove(target)
            # Channel for cities is offset 0
            channel = self.num_of_agents + (3 * self.agents.index(owner))
            self.map[target.y, target.x, channel] = 0

    def _update_map_with_new_city(self, agent, city):
        x, y = city.x, city.y
        # Update the map to reflect the new city
        city_channel = self.num_of_agents + (3 * self.agents.index(agent))  # Index for 'city' unit type
        self.map[y, x, city_channel] = 1
        # Update visibility and ownership if necessary
        self._update_visibility(agent, x, y)
        self.map[y, x, self.agents.index(agent)] = 1  # Mark ownership of the tile

    class City:
        def __init__(self, x, y, owner, env):
            self.x = x
            self.y = y
            self.health = 100
            self.resources = self._get_resources()
            self.finished_projects = [0 for _ in range(self.max_projects)]
            self.current_project = 0
            self.project_duration = 0
            self.owner = owner
            self.env = env

        def _get_resources(self):
            """
            Initialize resources for the city by scanning surrounding tiles.
            Returns a dictionary with resource types and their quantities.
            """
            resources = {'resource': 0, 'material': 1, 'water': 2}
            scan_range = 2  # Scan 2 tiles around the city

            for dx in range(-scan_range, scan_range + 1):
                for dy in range(-scan_range, scan_range + 1):
                    x, y = self.x + dx, self.y + dy
                    if 0 <= x < self.env.map_width and 0 <= y < self.env.map_height:
                        # Check resource channels
                        resource_channels_start = self.env.num_of_agents + 3 * self.env.num_of_agents
                        resources_channel = resource_channels_start
                        materials_channel = resource_channels_start + 1
                        water_channel = resource_channels_start + 2
                        if self.env.map[y, x, resources_channel] > 0:
                            resources['resource'] += 1
                        if self.env.map[y, x, materials_channel] > 0:
                            resources['material'] += 1
                        if self.env.map[y, x, water_channel] > 0:
                            resources['water'] += 1
            return resources
    
    def _calculate_num_channels(self):
        """
        Calculate the number of channels needed for the map representation, which changes dynamically based on number of players.
        """
        ownership_channels = self.num_of_agents  # One channel per agent for ownership
        units_channels = 3 * self.num_of_agents  # Cities, Warriors, Settlers per player
        resources_channels = 3  # Resources, Materials, Water
        return ownership_channels + units_channels + resources_channels

    def _calculate_city_attributes(self):
        """
        Calculate the number of attributes per city.
        Attributes:
            - Health
            - X location
            - Y location
            - Resources
            - Finished Projects (one-hot for each possible project)
            - Current Project
            - Project Duration
        """
        num_projects = self.max_projects  # Placeholder, needs to change
        return 1 + 2 + 3 + num_projects + 1 + 1  # Health, Location (x, y), Resources(3, 1 for each type), Finished Projects, Current Project, Duration

    
    def _initialize_map(self, seed=None):
        """
        Initialize the map with zeros or default values, place resources, and set spawn points for settlers warriors.
        Args:
            seed (int, optional): Seed for the random number generator to ensure reproducibility.
        """
        if seed is not None:
            np.random.seed(seed)
        
        num_channels = self._calculate_num_channels()
        self.map_height, self.map_width = self.map_size
        self.map = np.zeros((self.map_height, self.map_width, num_channels), dtype=np.float32)
    
        # Randomly place resources on the map
        self._place_resources()
    
        # Place spawn settlers and warriors for each player
        self._place_starting_units()

        # TODO: Implement more complex world generation and spawn point selection?
    
    def _get_agent_cities(self, agent):
        """
        Get the cities of the specified agent as a numpy array.
        Args:
            agent (str): The agent's name.

        Returns:
            np.ndarray: Array of shape (max_cities, num_city_attributes).
        """
        num_attributes = self._calculate_city_attributes()
        cities_obs = np.zeros((self.max_cities, num_attributes), dtype=np.float32)
        
        for idx, city in enumerate(self.cities[agent]):
            if idx >= self.max_cities:
                break  # Limit to max_cities
            
            # Extract city attributes
            city_data = [
                city.health,     # Health
                city.x,          # X coordinate
                city.y,          # Y coordinate
                city.resources.get('resource', 0),  # Resource
                city.resources.get('material', 0),  # Material
                city.resources.get('water', 0),      # Water
            ]
            
            # Finished Projects (Assuming it's a list of binary indicators)
            # If max_projects is 5, include only the first 5
            finished_projects = city.finished_projects[:self.max_projects]
            # Ensure that finished_projects has exactly max_projects elements
            while len(finished_projects) < self.max_projects:
                finished_projects.append(0)
            city_data.extend(finished_projects)
            
            # Current Project
            city_data.append(city.current_project)
            
            # Project Duration
            city_data.append(city.project_duration)
            
            # Assign to the cities_obs array
            cities_obs[idx] = city_data[:num_attributes]
        
        return cities_obs

    def _update_visibility(self, agent, unit_x, unit_y):
        visibility_range = self.visibility_range
        x_min = max(0, unit_x - visibility_range)
        x_max = min(self.map_width, unit_x + visibility_range + 1)
        y_min = max(0, unit_y - visibility_range)
        y_max = min(self.map_height, unit_y + visibility_range + 1)
        
        self.visibility_maps[agent][y_min:y_max, x_min:x_max] = True
    
    def _place_resources(self, bountifulness=0.15):
        """
        Randomly place resources, materials, and water on the map.
        """
        num_resources = int(bountifulness * self.map_height * self.map_width)
        resource_channels_start = self.num_of_agents + 3 * self.num_of_agents  # Starting index for resource channels, since this much will be occupied by borders and units

        # Channels for resources
        resources_channel = resource_channels_start  # Index for energy resources
        materials_channel = resource_channels_start + 1  # Index for materials
        water_channel = resource_channels_start + 2  # Index for water

        all_tiles = [(x, y) for x in range(self.map_width) for y in range(self.map_height)]
        np.random.shuffle(all_tiles)  # Shuffle the list to randomize tile selection
        # POSSIBLE BOTTLENECK!

        resources_placed = 0
        tile_index = 0

        while resources_placed < num_resources and tile_index < len(all_tiles):
            x, y = all_tiles[tile_index]
            tile_index += 1

            # Check if there is already a resource on this tile
            tile_resources = self.map[y, x, resources_channel:water_channel + 1]
            if np.any(tile_resources > 0):
                continue  

            # Randomly choose a resource type to place
            resource_type = np.random.choice(['resource', 'material', 'water'])
            if resource_type == 'resource':
                self.map[y, x, resources_channel] = 1
            elif resource_type == 'material':
                self.map[y, x, materials_channel] = 1
            elif resource_type == 'water':
                self.map[y, x, water_channel] = 1

            resources_placed += 1
        
    def _place_starting_units(self):
        """
        Place spawn points for settlers and starting units (e.g., warriors) for each player.
        """
        spawn_points = []
        for agent_idx in range(self.num_of_agents):
            while True:
                x = np.random.randint(0, self.map_width)
                y = np.random.randint(0, self.map_height)
                # Ensure the tile is empty (and not too close to other spawn points?)
                if self._is_tile_empty(x, y):
                    break
            spawn_points.append((x, y))
            self._place_unit(agent_idx, 'settler', x, y)
            adjacent_tiles = self._get_adjacent_tiles(x, y) # Put in the first possible tile
            warrior_placed = False
            for adj_x, adj_y in adjacent_tiles:
                if self._is_tile_empty(adj_x, adj_y):
                    # Place the warrior at (adj_x, adj_y)
                    self._place_unit(agent_idx, 'warrior', adj_x, adj_y)
                    warrior_placed = True
                    break
            if not warrior_placed:
                # Handle the case where no adjacent empty tile is found
                print(f"Warning: Could not place warrior for agent {agent_idx} adjacent to settler at ({x}, {y}).")
               # Optionally, expand search radius

    
    def _is_tile_empty(self, x, y):
        """
        Check if a tile is empty (no units, resources, or ownership).
        # TODO: Right now, this is **too** simple. It's fine if there are resources, just need to make sure it's not owned and there are no other units.
        It might be a good idea to make this return as to *what* is there (nothing, unit from player 2 + resource, etc.) and go on with that information.
        """
        return np.all(self.map[y, x, :] == 0)

    def _place_unit(self, agent_idx, unit_type, x, y):
        """
        Place a unit of a specific type for a given agent at the specified location.
        Args:
            agent_idx: Index of the agent.
            unit_type: 'city', 'warrior', or 'settler'.
            x, y: Coordinates to place the unit.
        """
        unit_types = {'city': 0, 'warrior': 1, 'settler': 2}
        if unit_type not in unit_types:
            raise ValueError(f"Invalid unit type: {unit_type}") #no typos!
        unit_channel = self.num_of_agents + (3 * agent_idx) + unit_types[unit_type]
        self.map[y, x, unit_channel] = 1
        # Create a Unit instance and add it to the agent's unit list
        unit = self.Unit(x, y, unit_type, self.agents[agent_idx], self)
        self.units[self.agents[agent_idx]].append(unit)
        self._update_visibility(self.agents[agent_idx], x, y)
    
    def _get_adjacent_tiles(self, x, y):
        """
        Get a list of adjacent tile coordinates to (x, y), considering map boundaries.
        TODO: Add a check for units of other players, to utilize this for attacking etc. as well. 
        """
        adjacent_coords = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue  # This is just the root tile
                adj_x, adj_y = x + dx, y + dy
                # Check if the adjacent tile is within map boundaries
                if 0 <= adj_x < self.map_width and 0 <= adj_y < self.map_height:
                    adjacent_coords.append((adj_x, adj_y))
        return adjacent_coords

    def render(self):
        """
        Visualize the current state of the map using Pygame.
        """
        for event in pygame.event.get():
            if event.type == QUIT:
                pygame.quit()
                return

        # Background
        self.screen.fill((0, 0, 0))  # Black background

        # Draw the grid and elements
        self._draw_grid()
        self._draw_elements()

        # Overlay visibility
        self._draw_visibility()

        pygame.display.flip()
        self.clock.tick(60)  # Limit to 60 fps

    def _draw_visibility(self):
        """
        Overlay a semi-transparent shade on tiles visible to each agent, visualizing fog of war.
        """
        agent_shades = [
            (255, 0, 0, 50),    # Red with alpha 50
            (0, 255, 0, 50),    # Green with alpha 50
            (0, 0, 255, 50),    # Blue with alpha 50
            (255, 255, 0, 50),  # Yellow with alpha 50
            (255, 0, 255, 50),  # Magenta with alpha 50
            (0, 255, 255, 50)   # Cyan with alpha 50
        ]

        for agent_idx, agent in enumerate(self.agents):
            shade_color = agent_shades[agent_idx]
            shade_surface = pygame.Surface((self.cell_size, self.cell_size), pygame.SRCALPHA)
            shade_surface.fill(shade_color)

            visibility_map = self.visibility_maps[agent]
            visible_tiles = np.argwhere(visibility_map)

            for y, x in visible_tiles:
                self.screen.blit(shade_surface, (x * self.cell_size, y * self.cell_size))
            #print(f"Agent {agent_idx} can see {len(visible_tiles)} tiles.") # Debugging
            #print(visible_tiles) # Debugging

        
    def _draw_grid(self):
        """
        Draw the grid lines on the screen.
        """
        for x in range(0, self.window_width, self.cell_size):
            pygame.draw.line(self.screen, (50, 50, 50), (x, 0), (x, self.window_height))
        for y in range(0, self.window_height, self.cell_size):
            pygame.draw.line(self.screen, (50, 50, 50), (0, y), (self.window_width, y))
    
    def _draw_elements(self):
        """
        Draw the settlers, warriors, ownership, and resources on the map.
        """
        # Define colors
        agent_colors = [
            (255, 0, 0),    # Red
            (0, 255, 0),    # Green
            (0, 0, 255),    # Blue
            (255, 255, 0),  # Yellow
            (255, 0, 255),  # Magenta
            (0, 255, 255)   # Cyan
        ]
        resource_colors = {
            'resource': (200, 200, 200),   # Light gray
            'material': (139, 69, 19),     # Brown
            'water': (0, 191, 255)         # Deep sky blue
        }
        # Draw ownership (background color of tiles)
        for y in range(self.map_height):
            for x in range(self.map_width):
                for agent_idx in range(self.num_of_agents):
                    if self.map[y, x, agent_idx] == 1:
                        color = agent_colors[agent_idx % len(agent_colors)]
                        rect = pygame.Rect(x * self.cell_size, y * self.cell_size, self.cell_size, self.cell_size)
                        pygame.draw.rect(self.screen, color, rect)
                        break  # Only one player can own a tile
        # Draw resources
        resource_channels_start = self.num_of_agents + 3 * self.num_of_agents
        resources_channel = resource_channels_start
        materials_channel = resource_channels_start + 1
        water_channel = resource_channels_start + 2
        for y in range(self.map_height):
            for x in range(self.map_width):
                # Resources
                if self.map[y, x, resources_channel] == 1:
                    self._draw_circle(x, y, resource_colors['resource'])
                if self.map[y, x, materials_channel] == 1:
                    self._draw_circle(x, y, resource_colors['material'])
                if self.map[y, x, water_channel] == 1:
                    self._draw_circle(x, y, resource_colors['water'])
        # Draw units
        for agent_idx in range(self.num_of_agents):
            unit_base_idx = self.num_of_agents + (3 * agent_idx)
            city_channel = unit_base_idx + 0    # 'city'
            warrior_channel = unit_base_idx + 1  # 'warrior'
            settler_channel = unit_base_idx + 2  # 'settler'
            # Cities
            city_positions = np.argwhere(self.map[:, :, city_channel] == 1)
            # Make the city color slightly darker than the agent color
            darker_color = tuple(
                max(0, min(255, int(c * 0.7))) for c in agent_colors[agent_idx % len(agent_colors)]
            )
            for y_pos, x_pos in city_positions:
                self._draw_star(x_pos, y_pos, darker_color)
            # Warriors
            warrior_positions = np.argwhere(self.map[:, :, warrior_channel] == 1)
            for y_pos, x_pos in warrior_positions:
                self._draw_triangle(x_pos, y_pos, agent_colors[agent_idx % len(agent_colors)])
            # Settlers
            settler_positions = np.argwhere(self.map[:, :, settler_channel] == 1)
            for y_pos, x_pos in settler_positions:
                self._draw_square(x_pos, y_pos, agent_colors[agent_idx % len(agent_colors)])

    def _draw_circle(self, x, y, color):
        """
        Draw a circle (resource) at the given map coordinates.
        """
        center_x = x * self.cell_size + self.cell_size // 2
        center_y = y * self.cell_size + self.cell_size // 2
        radius = self.cell_size // 4
        pygame.draw.circle(self.screen, color, (center_x, center_y), radius)

    def _draw_square(self, x, y, color):
        """
        Draw a square (settler) at the given map coordinates. # Placeholder
        """
        padding = self.cell_size // 8
        rect = pygame.Rect(
            x * self.cell_size + padding,
            y * self.cell_size + padding,
            self.cell_size - 2 * padding,
            self.cell_size - 2 * padding
        )
        pygame.draw.rect(self.screen, color, rect)

    def _draw_triangle(self, x, y, color):
        """
        Draw a triangle (warrior) at the given map coordinates.
        """
        half_size = self.cell_size // 2
        quarter_size = self.cell_size // 4
        center_x = x * self.cell_size + half_size
        center_y = y * self.cell_size + half_size
        points = [
            (center_x, center_y - quarter_size),  # Top point
            (center_x - quarter_size, center_y + quarter_size),  # Bottom left
            (center_x + quarter_size, center_y + quarter_size)   # Bottom right
        ]
        pygame.draw.polygon(self.screen, color, points)

    def _draw_star(self, x, y, color):
        """
        Draw a star (city) at the given map coordinates.
        """
        center_x = x * self.cell_size + self.cell_size // 2
        center_y = y * self.cell_size + self.cell_size // 2
        radius_outer = self.cell_size // 3
        radius_inner = self.cell_size // 6
        num_points = 5
        points = []
        for i in range(num_points * 2):
            angle = i * math.pi / num_points - math.pi / 2  # Rotate to point upwards
            if i % 2 == 0:
                r = radius_outer
            else:
                r = radius_inner
            px = center_x + r * math.cos(angle)
            py = center_y + r * math.sin(angle)
            points.append((px, py))
        pygame.draw.polygon(self.screen, color, points)
        
    def reset(self):
        """
        Reset the environment.
        """
        self.agents = self.possible_agents[:]
        self.units = {agent: [] for agent in self.agents}
        self.cities = {agent: [] for agent in self.agents}
        self.agent_selector = agent_selector(self.agents)
        self.current_agent = self.agent_selector.next()
        
        self._initialize_map()
        # Reset visibility maps
        self.visibility_maps = {agent: np.zeros((self.map_height, self.map_width), dtype=bool) for agent in self.agents}
        for agent in self.agents:
            for unit in self.units[agent]:
                self._update_visibility(agent, unit.x, unit.y)
            for city in self.cities[agent]:
                self._update_visibility(agent, city.x, city.y)

        # Initialize tracking variables
        self.last_attacker = None
        self.last_target_destroyed = False
        
        # Prepare initial observations
        observations = {agent: self.observe(agent) for agent in self.agents}
        
        # Initialize rewards, dones, and infos
        rewards = {agent: 0 for agent in self.agents}
        dones = {agent: False for agent in self.agents}
        dones['__all__'] = False
        infos = {agent: {} for agent in self.agents}
        
        return observations


# Testing 
if __name__ == "__main__":
    map_size = (15, 30) 
    num_agents = 4        
    env = Civilization(map_size, num_agents)
    env.reset()
    running = True
    while running:
        env.render()
        for event in pygame.event.get():
            if event.type == QUIT:
                running = False
    pygame.quit()   