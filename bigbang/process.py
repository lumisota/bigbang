import bigbang.graph as graph
from bigbang.parse import get_date
import pandas as pd
import datetime
import numpy as np
#import math
import pytz
#import pickle
#import os

# turn a list of parsed messages into
# a dataframe of message data, indexed
# by message-id, with column-names from
# headers
def messages_to_dataframe(messages):
    # extract data into a list of tuples -- records -- with
    # the Message-ID separated out as an index 
    pm = [(m.get('Message-ID'), 
           (m.get('From'),
            m.get('Subject'),
            get_date(m),
            m.get('In-Reply-To'),
            m.get('References'),
            m.get_payload()))
          for m in messages if m.get('Message-ID')]

    ids,records = zip(*pm)

    mdf = pd.DataFrame.from_records(list(records),
                                    index=list(ids),
                                    columns=['From',
                                             'Subject',
                                             'Date',
                                             'In-Reply-To',
                                             'References',
                                             'Body'])
    mdf.index.name = 'Message-ID'
                              
    return mdf


def process_messages(messages):
    dates = []
    froms = []
    broke = []
    
    for m in messages:
        m_from = m.get('From')
        froms.append(m_from)
        
        try:
            date = get_date(m)
            dates.append(date)
        except Exception as e:
            print e
            dates.append(pd.NaT)
            broke.append(m)
        
    return dates, froms, broke

def activity(messages,clean=True):
    mdf = messages_to_dataframe(messages)

    if clean:
        #unnecessary?
        mdf = mdf.dropna(subset=['Date'])
        mdf = mdf[mdf['Date'] <  datetime.datetime.now(pytz.utc)] # drop messages apparently in the future

    mdf2 = mdf[['From','Date']]
    mdf2['Date'] = mdf['Date'].apply(lambda x: x.toordinal())

    activity = mdf2.groupby(['From','Date']).size().unstack('From').fillna(0)

    new_date_range = np.arange(mdf2['Date'].min(),mdf2['Date'].max())
    #activity.set_index('Date')
    
    activity = activity.reindex(new_date_range,fill_value=0)

    return activity

def compute_ascendancy(messages,duration=50):
    print('compute ascendancy')
    dated_messages = {}

    for m in messages:
        d = get_date(m)

        if d is not None and d < datetime.datetime.now(pytz.utc):
            o = d.toordinal()        
            dated_messages[o] = dated_messages.get(o,[])
            dated_messages[o].append(m)

    days = [k for k in dated_messages.keys()]
    day_offset = min(days)
    epoch = max(days)-min(days)

    ascendancy = np.zeros([max(days)-min(days)+1])
    capacity = np.zeros(([max(days)-min(days)+1]))

    for i in range(epoch):
        min_d = min(days) + i
        max_d = min_d + duration

        block_messages = []

        for d in range(min_d,max_d):
            block_messages.extend(dated_messages.get(d,[]))

        b_IG = graph.messages_to_interaction_graph(block_messages)
        b_matrix = graph.interaction_graph_to_matrix(b_IG)

        ascendancy[min_d-day_offset] = graph.ascendancy(b_matrix)
        capacity[min_d-day_offset] = graph.capacity(b_matrix)

    return ascendancy, capacity

# This is a touch hacky.
# Better to use numpy convolve
# http://stackoverflow.com/questions/11352047/finding-moving-average-from-data-points-in-python
# or else pandas' built-in rolling_mean
# http://pandas.pydata.org/pandas-docs/stable/computation.html#moving-rolling-statistics-moments
def smooth(a,factor):
    k = np.zeros(len(a))
    for i in range(factor):
        k += np.roll(a,i)

    k = k / factor

    #TODO: need to trim the outsides

    return k[factor:-factor]

# create a matrix by applying func to pairwise combinations of elements in a Series
# returns a square matrix as a DataFrame
# should return a symmetric matrix if func(a,b) == func(b,a)
# should return the identity matrix if func == '=='
def matricize(series, func):
  matrix = pd.DataFrame(columns=series, index=series)
  for index, element in enumerate(series):
    for second_index, second_element in enumerate(series):
      matrix.iloc[index,second_index] = func(element, second_element)
  
  return matrix