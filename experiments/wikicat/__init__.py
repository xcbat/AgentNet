

__doc__="""
This experiment Wikipedia data sample on musicians, scraped via the scripts present in this directory. 
For each musician, we know a number of boolean features (factors) on various topics like 
 * whether or not he/she was active in 1990's, 2000's, etc.
 * whether or not he/she plays guitar, piano, etc
 * whether or not he/she was born in 50's, 60's, etc.
 * what wikipedia categories does he/she belong to
 etc.



In the initial moment of time, agent knows nothing about any of them.
At each turn, agent may decide to
 - "open" one of the hidden factors.
   - if the factor turns out to be 1, agent receives +3 reward for Wikipedia categories, +1 for other categories,
   - Otherwise, the reward equals -1 for Wikipedia categories, -1 for other categores
   - all these rewards are parameterisable during environment creation
 - decide to quit session
   - yields reward of 0 and ends the interaction.
   - all farther actions will have no effect until next session

It is expected, that in order to maximize it's expected reward, the agent
will converge to a strategy of polling several key features and then utilizing learned
dependencies between these factors and other ones. For example, if a particular genre was
popular during particular decays, it makes sense to poll genres and than "open" the corresponding
most probable years. 

The experiment setup contains a single class WikicatEnvironment that
implements both BaseEnvironment and BaseObjective.
"""

import os
experiment_path = '/'.join(__file__.split('/')[:-1])
dataset_path = os.path.join(experiment_path,"musicians_categorized.csv")

import pandas as pd
import numpy as np

import theano
import theano.tensor as T


from AgentNet.objective import BaseObjective
from AgentNet.environment import BaseEnvironment

from AgentNet.utils.tensor_ops import in1d
from AgentNet.utils import create_shared,set_shared


from collections import defaultdict

default_rewards = defaultdict(lambda:0)
default_rewards["attribute_positive"] = 1
default_rewards["attribute_negative"] = -1
default_rewards["category_positive"] = 3
default_rewards["category_negative"] = -1
default_rewards["repeated_poll"]=-0.5
default_rewards["end_action"]=0
default_rewards["end_action_if_no_category_predicted"]=0

class WikicatEnvironment(BaseObjective,BaseEnvironment):


    def __init__(self,rewards = default_rewards):
        
        #dummy initial values
        
        #fill shared variables with dummy values
        self.attributes = create_shared("X_attrs_data",np.zeros([1,1]),'uint8')
        self.categories =  create_shared("categories_data",np.zeros([1,1]),'uint8')
        self.batch_size = self.attributes.shape[0]

        

        #"end_session_now" action
        end_action = T.zeros([self.batch_size,1], dtype='uint8')

        #concatenate data and cast it to float to avoid gradient problems
        self.joint_data = T.concatenate([self.attributes,
                                          self.categories,
                                          end_action,
                                         ],axis=1).astype(theano.config.floatX)
    
        #indices
        self.category_action_ids = T.arange(
            self.attributes.shape[1],
            self.attributes.shape[1]+self.categories.shape[1]
        )
        
        #last action id corresponds to the "end session" action
        self.end_action_id = self.joint_data.shape[1]-1
        
        self.rw = rewards
        
        
        
        
    """reading/loading data"""
           
    def get_dataset(self,min_occurences = 15):
        """loads dataset; returns:
            attributes: np.array
            wikipedia cetegories: np.array
            action names: list(str)"""
        df = pd.DataFrame.from_csv(dataset_path)
        df =  df[df.values.sum(axis=1) > min_occurences]
        
        
        feature_names = list(df.columns)
        categorical_columns = np.nonzero(map(lambda s: s.startswith("category:"),feature_names))[0]
        attribute_columns = np.nonzero(map(lambda s: not s.startswith("category:"),feature_names))[0]

        data_cats = df.iloc[:,categorical_columns]
        data_attrs = df.iloc[:,attribute_columns]

        return data_attrs,data_cats, list(data_attrs.columns)+list(data_cats.columns)+["end_session_now"]
    

    def load_data_batch(self,attrs_batch,categories_batch):
        """load data into model"""
        set_shared(self.attributes,attrs_batch)
        set_shared(self.categories,categories_batch)
    def load_random_batch(self,attrs,cats,batch_size=10):
        """load batch_size random samples from given data attributes and categories"""
        
        attrs,cats = np.array(attrs),np.array(cats)

        assert len(attrs) == len(cats)
        batch_ids = np.random.randint(0,len(attrs),batch_size)
        self.load_data_batch(attrs[batch_ids],cats[batch_ids])

         
    """dimensions"""
    
    @property
    def observation_size(self):
        return int((self.joint_data.shape[1]+2).eval())
    @property
    def state_size(self):
        return int(self.joint_data.shape[1].eval())
    
    
    def get_whether_alive(self,observations_tensor):
        """Given observations, returns whether session has or has not ended.
        Returns uint8 [batch,time_tick] where 1 means session is alive and 0 means session ended already.
        Note that session is considered still alive while agent is commiting end_action
        """
        return T.eq(observations_tensor[:,:,1],0)
    
    
    
    """agent interaction"""
    
    def get_action_results(self,last_state,action,time_i):
        
        #state is a boolean vector: whether or not i-th action
        #was tried already during this session
        #last output[:,end_code] always remains 1 after first being triggered
        
        
        batch_range = T.arange(action.shape[0])

        session_active = T.eq(last_state[:,self.end_action_id],0)
        
        state_after_action = T.set_subtensor(last_state[batch_range,action],1)
        
        new_state = T.switch(
            session_active.reshape([-1,1]),
            state_after_action,
            last_state
        )
        
        
        
        observation = T.concatenate([
                self.joint_data[batch_range,action,None],#uint8[batch,1] response
                T.eq(session_active,0).reshape([-1,1]), #whether session is terminated by now
                T.extra_ops.to_one_hot(action,self.joint_data.shape[1]), #what action was commited
            ],axis=1)
        
        return new_state, observation

    def get_reward(self,session_states,session_actions,batch_i):
        """
        WARNING! this runs on a single session, not on a batch
        reward given for taking the action in current environment state
        arguments:
            session_states float[batch_id, memory_id]: environment state before taking action
            session_actions int[batch_id]: agent action at this tick
        returns:
            reward float[batch_id]: reward for taking action from the given state
        """
        time_range = T.arange(session_actions.shape[0])
        

        has_tried_already = session_states[time_range,session_actions]
        session_is_active = T.eq(session_states[:,self.end_action_id],0)
        
        has_finished_now = T.eq(session_actions,self.end_action_id)
        has_finished_now = T.set_subtensor(has_finished_now[-1],1)
        end_tick = has_finished_now.nonzero()[0][0]
        
        action_is_categorical = in1d(session_actions, self.category_action_ids)
        
        at_least_one_categorical_action = T.any(action_is_categorical[:end_tick])
        
        
        response = self.joint_data[batch_i,session_actions].ravel()
        
        #categorical and attributes
        reward_for_intermediate_action = T.switch(
            action_is_categorical,
            response*(self.rw["category_positive"]-self.rw["category_negative"]) + self.rw["category_negative"],
            response*(self.rw["attribute_positive"]-self.rw["attribute_negative"]) + self.rw["attribute_negative"]
        )
        reward_for_intermediate_action_first_time = T.switch(
                has_tried_already,
                self.rw["repeated_poll"],
                reward_for_intermediate_action,
            )

        #ending session
        reward_for_end_action = T.switch(at_least_one_categorical_action, #if chosen at least 1 category
                                          self.rw["end_action"],   #do not penalize
                                          self.rw["end_action_if_no_category_predicted"])  #else punish
        
        #include end action
        reward_for_action = T.switch(
            has_finished_now,
            reward_for_end_action,
            reward_for_intermediate_action_first_time,
        )
        
        
        final_reward = T.switch(
            session_is_active,
            reward_for_action,
            0,

            
        )
        
        
        return final_reward.astype(theano.config.floatX)
    
    
